"""Çağrı oturumunun seslendirme yanı: cümleyi / akışı çalma, söze girilince kesme, düşünürken klavye sesi, konuşma
bitince sonucu uygulama."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

import numpy as np

from sesli_asistan.libs.obs import log_text

from ..models import CallEvent, DialogTurn
from ..speech import keyboard_sound
from .models import (
    CallState,
    Event,
    TurnRecord,
)
from .pbx_actions import _describe

if TYPE_CHECKING:
    from .session import CallSession

log = logging.getLogger("sesli_asistan.gateway.core.call")   # adı değişmez (log süzgeçleri)


async def _first_of(q: asyncio.Queue, work: asyncio.Task) -> str | None:
    """Kuyruktaki sıradaki cümle; diyalog bitip kuyruk boşsa None."""
    if not q.empty():
        return q.get_nowait()
    if work.done():
        return None
    getter = asyncio.create_task(q.get())
    try:
        await asyncio.wait({getter, work}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        if not getter.done():
            getter.cancel()
    if getter.done() and not getter.cancelled():
        return getter.result()
    return q.get_nowait() if not q.empty() else None


_KEYBOARD = None


def _keyboard() -> np.ndarray:
    global _KEYBOARD
    if _KEYBOARD is None:
        _KEYBOARD = keyboard_sound()
    return _KEYBOARD


class SpeakingMixin:
    # oturumun bu dosyada değişen alanları (değerleri CallSession.__init__ verir; tipler mypy için)
    _speech: asyncio.Task | None
    _speech_id: int
    _speech_began: float
    _speech_meta: dict[str, Any]
    _tts_fail_streak: int

    async def _thinking[T](self: CallSession, work: Awaitable[T]) -> T:
        """Diyalog sürerken klavye sesi çalar (gecikmeli başlar), iş bitince keser."""
        if not self.cfg.thinking_sound:
            return await work
        filler = asyncio.create_task(self._filler(), name=f"dusunme-{self.call_id}")
        try:
            return await work
        finally:
            filler.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await filler

    async def _filler(self: CallSession) -> None:
        await asyncio.sleep(self.cfg.thinking_sound_delay_s)
        self._emit(CallEvent.THINKING_SOUND)
        pcm = self.thinking_pcm if self.thinking_pcm is not None else _keyboard()
        while True:
            for i in range(0, len(pcm), 4000):        # 0,5 sn'lik parçalar
                await self.media.play(pcm[i:i + 4000])

    def _start_stream_speech(self: CallSession, first: str, q: asyncio.Queue, work: asyncio.Task, heard_at: float,
                             heard: str, t0: float) -> None:
        """Diyalog sürerken konuşma (ADR-018). Olay döngüsü serbest kalır: söze girme ve DTMF işlenir. Turun
        eylemleri diyalog bitince (konuşmanın sonunda ya da söze girilince) uygulanır."""
        self.state = CallState.SPEAKING
        self._speech_id += 1
        self._speech_began = self.clock()
        meta: dict[str, Any] = {"id": self._speech_id, "turn": None, "work": work, "heard_at": heard_at, "heard": heard,
                "dialog_s": 0.0, "t0": t0}
        self._speech_meta = meta

        async def chunks() -> AsyncIterator[str]:
            spoken = [first]
            yield first
            while True:
                nxt = await _first_of(q, work)
                if nxt is None:
                    break
                spoken.append(nxt)
                yield nxt
            turn = await self._finish_work(meta)
            said = " ".join(spoken)
            rest = turn.text[len(said):].strip() if turn.text.startswith(said) else turn.text
            if rest:
                yield rest

        self._speech = asyncio.create_task(self._speak_stream(chunks(), self._speech_id),
                                           name=f"konusma-{self.call_id}")

    async def _finish_work(self: CallSession, meta: dict[str, Any]) -> DialogTurn:
        """Akışlı konuşmanın diyalog sonucu (bir kez): yönlendirme, ölçüm, log, ipuçları."""
        if meta["turn"] is None:
            try:
                # shield: söze girme konuşmayı iptal eder ama diyalog yarıda kalmamalı (geçmiş ve eylem tutarlı kalsın)
                turn: DialogTurn = await asyncio.shield(meta["work"])
            finally:
                self.dialog.sink = None
            turn = self._route(turn)
            meta["turn"] = turn
            meta["dialog_s"] = self.clock() - meta["t0"]
            log.info("[%s] tur (akışlı): %s → %s %s (%.2f sn)", self.call_id, log_text(meta["heard"]),
                     log_text(turn.text),
                     [a.type for a in turn.actions], meta["dialog_s"])
            if turn.text:
                self._emit(CallEvent.REPLY, text=turn.text)
            await self._update_hints()
        return meta["turn"]

    async def _speak_stream(self: CallSession, chunks: Any, speech_id: int) -> None:
        said_at = self.clock()
        completed, first, tts_ok, heard_any = False, None, True, True
        try:
            res = await self.speaker.say_stream(chunks, self.media)
            first, tts_ok, heard_any = res.first_audio_s, res.ok, res.sentences > 0
            completed = True
        finally:
            with contextlib.suppress(Exception):
                await chunks.aclose()
            self._q.put_nowait(Event("speech_done", {"speech_id": speech_id, "completed": completed,
                                                      "said_at": said_at, "first_audio_s": first,
                                                      "tts_ok": tts_ok, "heard_any": heard_any}, self.clock()))

    def _start_speech(self: CallSession, turn: DialogTurn, heard_at: float | None, heard: str, dialog_s: float =
    0.0) -> None:
        turn = self._route(turn)
        if self.state != CallState.GREETING:
            self.state = CallState.SPEAKING
        self._speech_id += 1
        self._speech_began = self.clock()
        self._speech_meta = {"id": self._speech_id, "turn": turn, "heard_at": heard_at, "heard": heard,
                             "dialog_s": dialog_s}
        self._speech = asyncio.create_task(self._speak(turn.text, self._speech_id), name=f"konusma-{self.call_id}")
        if turn.text:
            self._emit(CallEvent.REPLY, text=turn.text)

    async def _speak(self: CallSession, text: str, speech_id: int) -> None:
        said_at = self.clock()
        completed, first, tts_ok, heard_any = False, None, True, True
        try:
            if text:
                res = await self.speaker.say(text, self.media)
                first, tts_ok = res.first_audio_s, res.ok
                heard_any = res.sentences > 0 or res.from_library
            completed = True
        finally:
            # iptal edilse de döngüye haber verilir (barge-in)
            self._q.put_nowait(Event("speech_done", {"speech_id": speech_id, "completed": completed,
                                                      "said_at": said_at, "first_audio_s": first,
                                                      "tts_ok": tts_ok, "heard_any": heard_any}, self.clock()))

    async def _speech_done(self: CallSession, speech_id: int, completed: bool, said_at: float,
                           first_audio_s: float | None, tts_ok: bool = True, heard_any: bool = True) -> None:
        if self._speech_meta.get("id") != speech_id:
            return   # yerine yenisi başlatılmış (DTMF, acil operatör) konuşmanın bitişi
        meta, self._speech_meta = self._speech_meta, {}
        turn: DialogTurn = meta["turn"] if meta["turn"] is not None else await self._finish_work(meta)
        latency = None
        if meta["heard_at"] is not None and first_audio_s is not None:
            latency = round(said_at - meta["heard_at"] + first_audio_s, 3)
        if meta["heard_at"] is not None or meta["heard"]:
            rec = TurnRecord(meta["heard"], turn.text, [_describe(a) for a in turn.actions],
                             round(meta["dialog_s"], 3), latency, barged=not completed,
                             llm_calls=getattr(turn, "llm_calls", 0))
            self.stats.turns.append(rec)
            self._emit(CallEvent.TURN, **asdict(rec))
        if not tts_ok:
            self.stats.tts_failures += 1
            self._tts_fail_streak += 1
            if not heard_any or self._tts_fail_streak >= 2:
                # arayan cevabı duymadı (yalnızca "operatöre bağlıyorum" yedek anonsunu duydu) ya da TTS art arda
                # çöküyor: sohbet sürdürülemez
                log.error("[%s] TTS cevabı seslendiremedi (%d. kez): operatöre", self.call_id, self._tts_fail_streak)
                if self.state not in (CallState.HANDED_OFF, CallState.ENDED):
                    await self._send_operator("tts_hatasi")
                return
        else:
            self._tts_fail_streak = 0
        await self._apply(turn, completed)

    def _speaking(self: CallSession) -> bool:
        return self._speech is not None and not self._speech.done()

    def _stop_speech(self: CallSession) -> None:
        if self._speech is not None:
            self._speech.cancel()
