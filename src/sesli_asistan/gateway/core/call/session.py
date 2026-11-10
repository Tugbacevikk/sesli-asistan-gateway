"""Çağrı oturumu: dört görev (pompa, STT, bekçi, döngü), olay işleme, tur. Seslendirme `speaking.py`, santral
eylemleri `pbx_actions.py` (karışım sınıfları; yöntemler `self: CallSession` ile yazılır, ADR-034 ile aynı yol)."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sesli_asistan.libs.obs import CURRENT_CALL, log_text

from ..dialog import DialogSession
from ..level import LevelGate, frame_db
from ..models import CallEvent, DialogTurn, HangupAction, OperatorAction
from ..ports import MediaPort, PbxPort, SttError, SttPort, SttStream
from ..schedule import HOLIDAY, OPEN
from ..speech import Speaker
from ..thresholds import (
    BARGE_IN_FLOOR_DB,
    LEVEL_LEARN_CONF,
)
from .models import (
    CallConfig,
    CallState,
    CallStats,
    Event,
)
from .pbx_actions import PbxActionsMixin
from .speaking import SpeakingMixin, _first_of

log = logging.getLogger("sesli_asistan.gateway.core.call")   # adı değişmez (log süzgeçleri)


class CallSession(SpeakingMixin, PbxActionsMixin):
    def __init__(self, call_id: str, codec: str, media: MediaPort, stt: SttPort, speaker: Speaker,
                 dialog: DialogSession, pbx: PbxPort, cfg: CallConfig | None = None,
                 hotwords: list[str] | None = None, clock: Callable[[], float] = time.monotonic,
                 observer: Callable[[str, dict[str, Any]], None] | None = None, channel: str = "santral",
                 thinking_pcm: Any = None, office: str = OPEN, hotword_priority: list[str] | None = None):
        """observer(olay, veri): durum değişimi, duyulan metin, cevap, eylem, tur ölçümü, bitiş — arayüz için.
        Gözlemcideki hata çağrıyı etkilemez."""
        self.observer = observer
        self.office = office                      # acik | mesai_disi | tatil (çağrı başındaki durum, 5.1.3)
        self.thinking_pcm = thinking_pcm          # 8 kHz int16; None: kodla üretilen klavye sesi
        self.call_id, self.codec = call_id, codec
        self.media, self.stt, self.speaker, self.dialog, self.pbx = media, stt, speaker, dialog, pbx
        self.cfg = cfg or CallConfig()
        self.hotwords = list(hotwords or [])
        self.hotword_priority = list(hotword_priority or [])   # en çok aranan dahililer (ipucu sırası)
        self._hint_for: tuple = ()
        self.clock = clock
        self.level = LevelGate(self.cfg.barge_in_level_margin_db, BARGE_IN_FLOOR_DB)
        self._speech_at: float | None = None      # son "konuşma başladı" anı (arayan seviyesini öğrenmek için)
        self._ignore_next = False                 # kapının saymadığı söze girmenin sonucu cevaplanmaz
        self.state = CallState.GREETING
        self.stats = CallStats(channel=channel, started=datetime.now().isoformat(timespec="seconds"))
        self.started_at = clock()
        self._q: asyncio.Queue[Event] = asyncio.Queue()
        self._inputs: list[Event] = []          # sıradaki tur için duyulanlar
        self._speech: asyncio.Task | None = None
        self._speech_meta: dict[str, Any] = {}
        self._speech_id = 0
        self._speech_began = 0.0
        self._transfer_deadline: float | None = None
        self._stream: SttStream | None = None
        self._stop = asyncio.Event()
        self._work: asyncio.Task | None = None   # akışlı turda süren diyalog (çağrı biterse iptal edilir)
        self._listen_since = 0.0                 # dinlemeye geçildiği / arayanın son konuştuğu an
        self._caller_talking = False             # SPEECH_STARTED geldi, sonucu (FINAL/NOT_RECOGNIZED) gelmedi
        self._barged = False                     # son söz asistanın sözünü keserek başladı
        self._silences = 0                       # art arda sessiz dinleme sayısı
        self._tts_fail_streak = 0

    @property
    def state(self) -> CallState:
        return self._state

    @state.setter
    def state(self, value: CallState) -> None:
        if getattr(self, "_state", None) != value:
            self._state = value
            self._emit(CallEvent.STATE, state=value.value)

    def _emit(self, kind: CallEvent, **data: Any) -> None:
        if self.observer is not None:
            try:
                self.observer(kind, data)
            except Exception:
                log.exception("[%s] gözlemci hatası (%s)", self.call_id, kind)

    # --- dışarıdan (REST) ---------------------------------------------------------------------------------

    def transfer_result(self, status: str) -> None:
        self._q.put_nowait(Event("transfer_result", {"status": status}, self.clock()))

    def dtmf(self, digit: str) -> None:
        self._q.put_nowait(Event("dtmf", {"digit": digit}, self.clock()))

    def caller_text(self, text: str) -> None:
        """Arayanın sözü metin olarak (arayüzde yazarak deneme): STT'den gelmiş gibi işlenir. Asistan konuşuyorsa
        söze girmiş sayılır."""
        now = self.clock()
        self._q.put_nowait(Event("speech_started", {"text": "", "reason": "", "ms": 0}, now))
        self._q.put_nowait(Event("final", {"text": text.strip(), "reason": "", "ms": 0}, now))

    def hangup(self, reason: str = "hangup") -> None:
        """Santral çağrıyı bitirdi (DELETE). `run()` hemen döner."""
        if not self.stats.outcome:
            self.stats.outcome = "transferred" if reason == "transferred" else "hangup"
        self.stats.reason = self.stats.reason or reason
        self._stop.set()

    # --- yaşam döngüsü ------------------------------------------------------------------------------------

    async def run(self) -> CallStats:
        CURRENT_CALL.set(self.call_id)          # bu görevin ve alt görevlerinin log satırları bu çağrıya bağlanır
        tasks: list[asyncio.Task] = []
        try:
            try:
                self._stream = await self.stt.open(self.call_id, self.codec, self.hotwords)
            except SttError as e:
                log.error("[%s] STT akışı açılamadı (%s): operatöre", self.call_id, e)
                await self._emergency_operator("stt_yok")
            else:
                tasks = [asyncio.create_task(self._pump(), name=f"pompa-{self.call_id}"),
                         asyncio.create_task(self._stt_events(), name=f"stt-{self.call_id}"),
                         asyncio.create_task(self._ticks(), name=f"bekci-{self.call_id}"),
                         asyncio.create_task(self._loop(), name=f"dongu-{self.call_id}")]
                if self.office != OPEN and self.cfg.after_hours_mode == "announce":
                    self._start_speech(self._closed_turn(), heard_at=None, heard="")
                else:
                    self._start_speech(self.dialog.greeting(), heard_at=None, heard="")
            try:
                # güvenlik: santral DELETE göndermezse oturum sonsuza kadar açık kalmasın
                await asyncio.wait_for(self._stop.wait(), self.cfg.max_call_duration_s + 60)
            except TimeoutError:
                log.error("[%s] oturum süre sınırını aştı, santral kapatmadı: siliniyor", self.call_id)
                self.stats.outcome = self.stats.outcome or "error"
                self.stats.reason = self.stats.reason or "oturum_suresi"
        finally:
            self.state = CallState.ENDED
            if self._speech is not None:
                self._speech.cancel()
            if self._work is not None:
                self._work.cancel()               # akışlı turun diyaloğu çağrıdan uzun yaşamasın
            for t in tasks:
                t.cancel()
            for t in [*tasks, *(x for x in (self._speech, self._work) if x is not None)]:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t
            if self._stream is not None:
                self.stats.stt_dropped = getattr(self._stream, "dropped", 0)
                cancel = getattr(self._stream, "cancel", None)
                if cancel:
                    cancel()
            self.stats.rtp = self.media.stats()
            self.stats.duration_s = round(self.clock() - self.started_at, 1)
            self.stats.misses = list(self.dialog.state.misses)
            self.media.close()
            self._emit(CallEvent.ENDED, outcome=self.stats.outcome, reason=self.stats.reason)
            log.info("[%s] çağrı bitti: %s (%s), %d tur, %d söze girme", self.call_id, self.stats.outcome,
                     self.stats.reason, len(self.stats.turns), self.stats.barge_ins)
        return self.stats

    # --- görevler -----------------------------------------------------------------------------------------

    async def _pump(self) -> None:
        assert self._stream is not None
        async for frame in self.media.frames():
            if self.cfg.barge_in_level_margin_db > 0 or self.cfg.background_margin_db > 0:
                self.level.add(self.clock(), frame_db(frame, self.codec))
            await self._stream.send(frame)

    async def _stt_events(self) -> None:
        assert self._stream is not None
        try:
            async for ev in self._stream.events():
                self._q.put_nowait(Event(ev.type, {"text": ev.text, "reason": ev.reason, "confidence": ev.confidence,
                                                    "ms": max(0, ev.end_ms - ev.start_ms)}, self.clock()))
        except SttError as e:
            self._q.put_nowait(Event("stt_error", {"error": str(e)}, self.clock()))
            return
        if not self._stop.is_set():
            # STT akışı hatasız ama erken bitti (servis kapanıyor): arayan sessizlikte kalmasın
            self._q.put_nowait(Event("stt_error", {"error": "akış beklenmedik şekilde bitti"}, self.clock()))

    async def _ticks(self) -> None:
        while True:
            await asyncio.sleep(self.cfg.tick_s)
            self._q.put_nowait(Event("tick", at=self.clock()))

    async def _loop(self) -> None:
        while True:
            ev = await self._q.get()
            try:
                await self._handle(ev)
                if self.state == CallState.LISTENING and self._inputs and not self._speaking():
                    await self._turn()
            except asyncio.CancelledError:
                raise
            except Exception:
                # bir hatanın çağrıyı sessizlikte bırakmasına izin verme (izole hata, tasarim.md 9.2)
                log.exception("[%s] beklenmeyen hata (olay: %s): operatöre", self.call_id, ev.kind)
                await self._emergency_operator("ic_hata")

    # --- olaylar ------------------------------------------------------------------------------------------

    async def _handle(self, ev: Event) -> None:
        """Olay → işleyicisi. Arayan sözü olayları önce dinleme zamanlayıcılarını sıfırlar."""
        k = ev.kind
        if k in ("speech_started", "final", "not_recognized"):
            self._caller_talking = k == "speech_started"
            self._listen_since = ev.at
            self._silences = 0
        if k == "speech_started":
            self._on_speech_started(ev)
        elif k in ("final", "not_recognized"):
            self._on_heard(ev, k)
        elif k == "speech_done":
            await self._speech_done(**ev.data)
        elif k == "transfer_result":
            await self._transfer_result(ev.data["status"])
        elif k == "dtmf":
            self._on_dtmf(ev)
        elif k == "stt_error":
            await self._on_stt_error(ev)
        elif k == "tick":
            await self._tick(ev.at)

    def _on_speech_started(self, ev: Event) -> None:
        """Arayan konuşmaya başladı: asistan konuşuyorsa söze girme (ses seviyesi kapısından geçerse)."""
        self._speech_at = ev.at
        # konuşma başlamadan önce algılanmış ses (asistan düşünürken) bu konuşmayı kesmez
        if (self._speaking() and self.cfg.barge_in and ev.at >= self._speech_began
                and self.state in (CallState.SPEAKING, CallState.GREETING) and not self._closing()):
            if not self.level.allows_barge_in(ev.at):
                # arka plan sesi (odadaki video, ikinci konuşan): arayanın kendi seviyesinin çok altında
                log.info("[%s] söze girme sayılmadı: ses seviyesi düşük (%.1f dBFS, arayan %s)", self.call_id,
                         self.level.recent_db(ev.at) or -120,
                         f"{self.level.caller_db:.1f} dBFS" if self.level.caller_db is not None else "bilinmiyor")
                self.stats.barge_ins_ignored += 1
                self._ignore_next = True
                return
            log.info("[%s] söze girildi: seslendirme durduruluyor", self.call_id)
            self.stats.barge_ins += 1
            self._emit(CallEvent.BARGE_IN)
            self._stop_speech()
            self._barged = True             # bu sözün başı asistanın sesiyle karıştı (anlaşılamazsa sayılmaz)

    def _on_heard(self, ev: Event, k: str) -> None:
        """STT sonucu (metin ya da anlaşılamadı): gürültü, arka plan ve kapının saymadığı sözler elenir; kalan söz
        sıradaki tura girer."""
        if k == "not_recognized" and ev.data["reason"] == "empty" and ev.data["ms"] < self.cfg.min_noise_ms:
            return  # öksürük, tık: cevaplanmaz, deneme sayılmaz
        if k == "final" and self._noise_fragment(ev.data):
            log.info("[%s] gürültü kırıntısı yok sayıldı (güven %.2f)", self.call_id,
                     ev.data.get("confidence") or 0)
            return
        if self._ignore_next:
            # kapının saymadığı söze girmenin metni (arka plan): cevaplanmaz, deneme sayılmaz
            self._ignore_next = False
            log.info("[%s] arka plan sesinin sonucu yok sayıldı", self.call_id)
            return
        if (k == "final" and self._speech_at is not None
                and self.level.is_background(self._speech_at, ev.at, self.cfg.background_margin_db)
                and not self.dialog.concerns_call(ev.data.get("text", ""))):
            # odadaki başka biri: arayanın kendi seviyesinin çok altında (ofiste deneme 09.11.2026). Rehberde bir
            # hedefi anan ya da açık soruya cevap veren söz kısık da olsa arayanındır (canlı deneme 10.11.2026)
            log.info("[%s] arka plan konuşması yok sayıldı (%.1f dBFS, arayan %.1f dBFS): %s", self.call_id,
                     self.level.utterance_db(self._speech_at, ev.at) or -120, self.level.caller_db or -120,
                     ev.data.get("text", "")[:60])
            self.stats.background_ignored += 1
            return
        if (k == "final" and self._speech_at is not None
                and (ev.data.get("confidence") or 0) >= LEVEL_LEARN_CONF):
            self.level.learn(self._speech_at, ev.at)         # anlaşılan söz: arayanın kendi sesi
        if self.state in (CallState.GREETING, CallState.LISTENING, CallState.THINKING, CallState.SPEAKING):
            self._inputs.append(ev)

    def _on_dtmf(self, ev: Event) -> None:
        if ev.data["digit"] == self.cfg.dtmf_operator_digit and self.state not in (
                CallState.HANDED_OFF, CallState.TRANSFERRING) and not self._closing():
            log.info("[%s] DTMF %s: operatöre", self.call_id, ev.data["digit"])
            if self._speaking():
                self._stop_speech()
            self._start_speech(self._operator_turn("dtmf"), heard_at=None, heard="[DTMF]")

    async def _on_stt_error(self, ev: Event) -> None:
        log.error("[%s] STT akışı koptu (%s): operatöre", self.call_id, ev.data["error"])
        if self.state not in (CallState.HANDED_OFF, CallState.TRANSFERRING):
            await self._emergency_operator("stt_koptu")

    def _noise_fragment(self, data: dict) -> bool:
        conf = data.get("confidence")
        letters = sum(ch.isalpha() for ch in data.get("text") or "")
        return (self.cfg.noise_below > 0 and conf is not None and 0 < conf < self.cfg.noise_below
                and letters <= self.cfg.noise_max_letters)

    async def _turn(self) -> None:
        inputs, self._inputs = self._inputs, []
        finals = [e for e in inputs if e.kind == "final" and e.data["text"]]
        heard = " ".join(e.data["text"] for e in finals)
        confs = [e.data["confidence"] for e in finals if e.data.get("confidence")]
        # karar tam değerle (yuvarlama eşiği aşırmasın: canlı deneme 03.11.2026, güven 0,495 → 0,5 sayılıp tekrar
        # istenmedi), arayüze iki basamak; yazarak denemede (güven yok) teyit sorulmaz
        conf = min(confs) if confs else None
        self._emit(CallEvent.HEARD, text=heard, confidence=round(conf, 2) if conf is not None else None)
        self.dialog.after_barge_in, self._barged = self._barged, False
        self.dialog.stt_confidence = conf
        self.state = CallState.THINKING
        t0 = self.clock()
        # Mesai dışında akış yok: operatör eylemi diyalog bitince "operatörümüz yok"a çevrilir (`_route`); LLM'in
        # önceden seslendirilen "Sizi operatöre bağlıyorum" cümlesi geri alınamazdı (çelişkili seslendirme).
        if getattr(self.dialog.agent.cfg, "stream_replies", False) and self.office == OPEN:
            # ADR-018: LLM cevabının ilk cümlesi hazır olunca konuşmaya başla, kalanı geldikçe
            q: asyncio.Queue[str] = asyncio.Queue()
            self.dialog.sink = q.put_nowait
            work = asyncio.create_task(self.dialog.handle_user(heard), name=f"diyalog-{self.call_id}")
            self._work = work
            first = await self._thinking(_first_of(q, work))
            if first is not None:
                self._start_stream_speech(first, q, work, heard_at=inputs[0].at, heard=heard, t0=t0)
                return
            self.dialog.sink = None
            turn = await work
        else:
            turn = await self._thinking(self.dialog.handle_user(heard))
        dialog_s = self.clock() - t0
        log.info("[%s] tur: %s → %s %s (%.2f sn)", self.call_id, log_text(heard), log_text(turn.text),
                 [a.type for a in turn.actions], dialog_s)
        await self._update_hints()
        self._start_speech(turn, heard_at=inputs[0].at, heard=heard, dialog_s=dialog_s)


    def _route(self, turn: DialogTurn) -> DialogTurn:
        if self.office != OPEN and any(isinstance(a, OperatorAction) for a in turn.actions):
            # mesai dışı: operatör yok — "operatöre bağlıyorum" yerine bilgi verip kapat
            reason = next(a.reason for a in turn.actions if isinstance(a, OperatorAction))
            turn = DialogTurn(self.dialog.agent.cfg.texts.no_operator, [HangupAction(f"operator_yok:{reason}")],
                              fallback=True)
        return turn


    async def _tick(self, now: float) -> None:
        if self.state == CallState.HANDED_OFF:
            return
        if self.media.idle_s() > self.cfg.rtp_idle_timeout_s:
            log.warning("[%s] %.0f sn RTP gelmedi: çağrı kapatılıyor", self.call_id, self.media.idle_s())
            self.stats.outcome, self.stats.reason = "error", "rtp_zaman_asimi"
            self._stop.set()
        elif self._transfer_deadline is not None and now > self._transfer_deadline:
            log.warning("[%s] aktarma sonucu %.0f sn gelmedi: başarısız sayılıyor", self.call_id,
                        self.cfg.transfer_result_timeout_s)
            await self._transfer_result("failed")
        elif now - self.started_at > self.cfg.max_call_duration_s and self.state == CallState.LISTENING:
            log.warning("[%s] çağrı süresi aşıldı: operatöre", self.call_id)
            self._start_speech(self._operator_turn("sure_asimi"), heard_at=None, heard="[süre]")
        elif (self.state == CallState.LISTENING and not self._inputs and not self._caller_talking
              and not self._speaking() and now - self._listen_since > self.cfg.no_input_s):
            self._silences += 1
            self.stats.no_inputs += 1
            log.info("[%s] arayan %.0f sn konuşmadı (%d. kez)", self.call_id, self.cfg.no_input_s, self._silences)
            if self._silences >= self.cfg.no_input_max:
                self._start_speech(self._operator_turn("sessiz"), heard_at=None, heard="[sessizlik]")
            else:
                self._start_speech(self.dialog.no_input_turn(),
                                   heard_at=None, heard="[sessizlik]")

    # --- yardımcılar --------------------------------------------------------------------------------------

    def _listen(self) -> None:
        self.state = CallState.LISTENING
        self._listen_since = self.clock()


    async def _update_hints(self) -> None:
        """Rehber araması aday döndürdüyse STT ipuçlarında adaylar öne alınır: arayan sorulan soruya ("hangi
        Ayşe?") büyük olasılıkla bunlardan biriyle cevap verir. Büyük rehberde bu adlar başlangıç listesine
        sığmamış olabilir (tasarim.md 5.2.1)."""
        cands = self.dialog.state.last_results
        setter = getattr(self._stream, "set_hotwords", None)
        if not cands or cands == self._hint_for or setter is None:
            return
        self._hint_for = cands
        words = self.dialog.agent.tools.index.hotwords(priority=self.hotword_priority, focus=cands)
        try:
            await setter(words)
        except Exception as e:      # ipucu yalnızca iyileştirme: çağrıyı etkilemez
            log.warning("[%s] STT ipuçları güncellenemedi: %s", self.call_id, e)

    def _closed_turn(self) -> DialogTurn:
        t = self.dialog.agent.cfg.texts
        return DialogTurn(t.holiday if self.office == HOLIDAY else t.closed, [HangupAction(self.office)],
                          fallback=True)

    def _closing(self) -> bool:
        """Kapanış anonsu çalıyor: söze girilse de kesilmez (arayan bilgiyi duymalı, ardından çağrı kapanır)."""
        turn = self._speech_meta.get("turn")
        return turn is not None and any(isinstance(a, HangupAction) for a in turn.actions)


