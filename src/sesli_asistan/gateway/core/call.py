"""Tek bir çağrının yönetimi: durum makinesi (tasarim.md 5.1.1), akışlar (7.1–7.4), hata modları (9.1).

Bir çağrıda dört görev çalışır ve birbirini beklemez:
  1. pompa    : RTP kareleri → STT akışı (ses hiçbir zaman ağır bir işle aynı akışta okunmaz; 6.1)
  2. STT      : STT olaylarını olay kuyruğuna koyar
  3. bekçi    : her saniye "tik" (RTP boşta kalma, çağrı süresi, aktarma sonucu zaman aşımı)
  4. döngü    : olayları **sırayla** işler; durum yalnızca burada değişir (yarış koşulu yok)
Konuşma (TTS → RTP) ayrı bir görevdir; böylece arayan söze girince (SPEECH_STARTED) döngü onu iptal edebilir.

Kurallar (kodla):
  - Arayan, asistan konuşurken söze girerse seslendirme durur (barge-in, FR-11). Söze girilen cümle bir aktarma
    cümlesiyse ("Sizi Ayşe Yılmaz'a aktarıyorum") aktarma **yapılmaz**: arayan büyük olasılıkla düzeltiyor
    ("hayır, satış"); bekleyen aktarma silinir, yeni cümle normal işlenir (tasarim.md 7.5, ADR-012).
  - Operatöre aktarma söze girilse de yapılır (güvenli son çare).
  - STT "anlaşılamadı" derse diyalog "anlayamadım" der ve deneme sayacı artar; çok kısa gürültüler sayılmaz.
  - Arayan, asistan düşünürken konuşursa cümleler sıraya girer; sonraki turda birleştirilip işlenir.
  - STT akışı açılamaz ya da koparsa, santral eylemi başarısız olursa: hata anonsu + operatör (zarif bozulma).
  - Asistan düşünürken (LLM + rehber) `thinking_sound_delay_s`'den uzun sürerse arayana hafif bir klavye sesi
    çalınır; cevap hazır olunca o pakette kesilir. Telefonda 3–5 sn'lik sessizlik hattın koptuğu sanılmasın.
  - TTS cevabı seslendiremezse ve arayan cevaptan hiçbir şey duymadıysa operatöre aktarılır (yedek anons zaten
    "operatöre bağlıyorum" der; tasarim.md 9.1). Kısmen duyduysa çağrı sürer, art arda ikinci hatada operatör.
  - Arayan dinleme sırasında `no_input_s` boyunca hiç konuşmazsa "Sizi duyamıyorum…" denir; `no_input_max`'ıncı
    sessizlikte operatöre aktarılır (santral sessizken de RTP gönderdiği için RTP zaman aşımı bunu yakalamaz).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from sesli_asistan.libs.obs import log_text

from .dialog import DialogSession
from .models import Action, DialogTurn, HangupAction, OperatorAction, TransferAction
from .ports import MediaPort, PbxError, PbxPort, SttError, SttPort, SttStream
from .schedule import HOLIDAY, OPEN
from .speech import Speaker, keyboard_sound

log = logging.getLogger(__name__)


class CallState(StrEnum):
    GREETING = "greeting"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    TRANSFERRING = "transferring"   # aktarma eylemi santralde, sonuç bekleniyor
    HANDED_OFF = "handed_off"       # aktarıldı / operatöre verildi: santralin DELETE'i beklenir
    ENDED = "ended"


TRANSFER_STATUSES = ("answered", "busy", "no_answer", "failed")


@dataclass(frozen=True)
class CallConfig:
    rtp_idle_timeout_s: float = 20.0
    max_call_duration_s: float = 600.0
    transfer_result_timeout_s: float = 30.0
    barge_in: bool = True
    min_noise_ms: int = 600            # bundan kısa "anlaşılamadı" (boş) sesler gürültü sayılır, cevaplanmaz
    dtmf_operator_digit: str = "0"     # FR-15: tuşlayınca operatör
    no_input_s: float = 8.0            # dinlemede bu kadar sessizlik → "Sizi duyamıyorum"
    no_input_max: int = 2              # kaçıncı sessizlikte operatöre
    tick_s: float = 1.0
    thinking_sound: bool = True        # düşünürken klavye sesi (sessizlik "hat koptu mu?" dedirtmesin)
    thinking_sound_delay_s: float = 0.4   # cevap bundan hızlı gelirse ses hiç çalınmaz
    after_hours_mode: str = "announce"    # mesai dışı: announce (anons + kapat) | assistant (operatör yerine anons)


@dataclass
class TurnRecord:
    """Bir turun ölçümleri (log ve uçtan uca ölçüm aracı için)."""

    heard: str
    reply: str
    actions: list[str]
    dialog_s: float                    # LLM + araçlar
    final_to_audio_s: float | None     # STT metni geldi → arayana ilk ses (turn_latency, tasarim.md 12.2)
    barged: bool = False
    llm_calls: int = 0                 # 0: tur kodla çözüldü (hızlı yol, soru, seçim, sabit cümle)


@dataclass
class CallStats:
    turns: list[TurnRecord] = field(default_factory=list)
    barge_ins: int = 0
    tts_failures: int = 0
    no_inputs: int = 0
    outcome: str = ""                  # transferred | operator | closed | hangup | error
    reason: str = ""
    stt_dropped: int = 0
    rtp: dict[str, int] = field(default_factory=dict)
    channel: str = "santral"           # santral | web (arayüzden deneme)
    started: str = ""                  # yerel saat, ISO
    duration_s: float = 0.0
    misses: list[str] = field(default_factory=list)   # rehberde bulunamayan aramalar


@dataclass
class _Event:
    kind: str
    data: dict[str, Any] = field(default_factory=dict)
    at: float = 0.0


class CallSession:
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
        self.state = CallState.GREETING
        self.stats = CallStats(channel=channel, started=datetime.now().isoformat(timespec="seconds"))
        self.started_at = clock()
        self._q: asyncio.Queue[_Event] = asyncio.Queue()
        self._inputs: list[_Event] = []          # sıradaki tur için duyulanlar
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
        self._silences = 0                       # art arda sessiz dinleme sayısı
        self._tts_fail_streak = 0

    @property
    def state(self) -> CallState:
        return self._state

    @state.setter
    def state(self, value: CallState) -> None:
        if getattr(self, "_state", None) != value:
            self._state = value
            self._emit("state", state=value.value)

    def _emit(self, kind: str, **data: Any) -> None:
        if self.observer is not None:
            try:
                self.observer(kind, data)
            except Exception:
                log.exception("[%s] gözlemci hatası (%s)", self.call_id, kind)

    # --- dışarıdan (REST) ---------------------------------------------------------------------------------

    def transfer_result(self, status: str) -> None:
        self._q.put_nowait(_Event("transfer_result", {"status": status}, self.clock()))

    def dtmf(self, digit: str) -> None:
        self._q.put_nowait(_Event("dtmf", {"digit": digit}, self.clock()))

    def caller_text(self, text: str) -> None:
        """Arayanın sözü metin olarak (arayüzde yazarak deneme): STT'den gelmiş gibi işlenir. Asistan konuşuyorsa
        söze girmiş sayılır."""
        now = self.clock()
        self._q.put_nowait(_Event("speech_started", {"text": "", "reason": "", "ms": 0}, now))
        self._q.put_nowait(_Event("final", {"text": text.strip(), "reason": "", "ms": 0}, now))

    def hangup(self, reason: str = "hangup") -> None:
        """Santral çağrıyı bitirdi (DELETE). `run()` hemen döner."""
        if not self.stats.outcome:
            self.stats.outcome = "transferred" if reason == "transferred" else "hangup"
        self.stats.reason = self.stats.reason or reason
        self._stop.set()

    # --- yaşam döngüsü ------------------------------------------------------------------------------------

    async def run(self) -> CallStats:
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
            self._emit("ended", outcome=self.stats.outcome, reason=self.stats.reason)
            log.info("[%s] çağrı bitti: %s (%s), %d tur, %d söze girme", self.call_id, self.stats.outcome,
                     self.stats.reason, len(self.stats.turns), self.stats.barge_ins)
        return self.stats

    # --- görevler -----------------------------------------------------------------------------------------

    async def _pump(self) -> None:
        assert self._stream is not None
        async for frame in self.media.frames():
            await self._stream.send(frame)

    async def _stt_events(self) -> None:
        assert self._stream is not None
        try:
            async for ev in self._stream.events():
                self._q.put_nowait(_Event(ev.type, {"text": ev.text, "reason": ev.reason,
                                                    "ms": max(0, ev.end_ms - ev.start_ms)}, self.clock()))
        except SttError as e:
            self._q.put_nowait(_Event("stt_error", {"error": str(e)}, self.clock()))
            return
        if not self._stop.is_set():
            # STT akışı hatasız ama erken bitti (servis kapanıyor): arayan sessizlikte kalmasın
            self._q.put_nowait(_Event("stt_error", {"error": "akış beklenmedik şekilde bitti"}, self.clock()))

    async def _ticks(self) -> None:
        while True:
            await asyncio.sleep(self.cfg.tick_s)
            self._q.put_nowait(_Event("tick", at=self.clock()))

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

    async def _handle(self, ev: _Event) -> None:
        k = ev.kind
        if k in ("speech_started", "final", "not_recognized"):
            self._caller_talking = k == "speech_started"
            self._listen_since = ev.at
            self._silences = 0
        if k == "speech_started":
            # konuşma başlamadan önce algılanmış ses (asistan düşünürken) bu konuşmayı kesmez
            if (self._speaking() and self.cfg.barge_in and ev.at >= self._speech_began
                    and self.state in (CallState.SPEAKING, CallState.GREETING) and not self._closing()):
                log.info("[%s] söze girildi: seslendirme durduruluyor", self.call_id)
                self.stats.barge_ins += 1
                self._emit("barge_in")
                self._speech.cancel()
        elif k in ("final", "not_recognized"):
            if k == "not_recognized" and ev.data["reason"] == "empty" and ev.data["ms"] < self.cfg.min_noise_ms:
                return  # öksürük, tık: cevaplanmaz, deneme sayılmaz
            if self.state in (CallState.GREETING, CallState.LISTENING, CallState.THINKING, CallState.SPEAKING):
                self._inputs.append(ev)
        elif k == "speech_done":
            await self._speech_done(**ev.data)
        elif k == "transfer_result":
            await self._transfer_result(ev.data["status"])
        elif k == "dtmf":
            if ev.data["digit"] == self.cfg.dtmf_operator_digit and self.state not in (
                    CallState.HANDED_OFF, CallState.TRANSFERRING) and not self._closing():
                log.info("[%s] DTMF %s: operatöre", self.call_id, ev.data["digit"])
                if self._speaking():
                    self._speech.cancel()
                self._start_speech(self._operator_turn("dtmf"), heard_at=None, heard="[DTMF]")
        elif k == "stt_error":
            log.error("[%s] STT akışı koptu (%s): operatöre", self.call_id, ev.data["error"])
            if self.state not in (CallState.HANDED_OFF, CallState.TRANSFERRING):
                await self._emergency_operator("stt_koptu")
        elif k == "tick":
            await self._tick(ev.at)

    async def _turn(self) -> None:
        inputs, self._inputs = self._inputs, []
        heard = " ".join(e.data["text"] for e in inputs if e.kind == "final" and e.data["text"])
        self._emit("heard", text=heard)
        self.state = CallState.THINKING
        t0 = self.clock()
        if getattr(self.dialog.agent.cfg, "stream_replies", False):
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

    async def _thinking(self, work):
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

    async def _filler(self) -> None:
        await asyncio.sleep(self.cfg.thinking_sound_delay_s)
        self._emit("thinking_sound")
        pcm = self.thinking_pcm if self.thinking_pcm is not None else _keyboard()
        while True:
            for i in range(0, len(pcm), 4000):        # 0,5 sn'lik parçalar
                await self.media.play(pcm[i:i + 4000])

    def _route(self, turn: DialogTurn) -> DialogTurn:
        if self.office != OPEN and any(isinstance(a, OperatorAction) for a in turn.actions):
            # mesai dışı: operatör yok — "operatöre bağlıyorum" yerine bilgi verip kapat
            reason = next(a.reason for a in turn.actions if isinstance(a, OperatorAction))
            turn = DialogTurn(self.dialog.agent.cfg.texts.no_operator, [HangupAction(f"operator_yok:{reason}")],
                              fallback=True)
        return turn

    def _start_stream_speech(self, first: str, q: asyncio.Queue, work: asyncio.Task, heard_at: float,
                             heard: str, t0: float) -> None:
        """Diyalog sürerken konuşma (ADR-018). Olay döngüsü serbest kalır: söze girme ve DTMF işlenir. Turun
        eylemleri diyalog bitince (konuşmanın sonunda ya da söze girilince) uygulanır."""
        self.state = CallState.SPEAKING
        self._speech_id += 1
        self._speech_began = self.clock()
        meta = {"id": self._speech_id, "turn": None, "work": work, "heard_at": heard_at, "heard": heard,
                "dialog_s": 0.0, "t0": t0}
        self._speech_meta = meta

        async def chunks():
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

    async def _finish_work(self, meta: dict[str, Any]) -> DialogTurn:
        """Akışlı konuşmanın diyalog sonucu (bir kez): yönlendirme, ölçüm, log, ipuçları."""
        if meta["turn"] is None:
            try:
                # shield: söze girme konuşmayı iptal eder ama diyalog yarıda kalmamalı (geçmiş ve eylem tutarlı kalsın)
                turn = await asyncio.shield(meta["work"])
            finally:
                self.dialog.sink = None
            meta["turn"] = self._route(turn)
            meta["dialog_s"] = self.clock() - meta["t0"]
            turn = meta["turn"]
            log.info("[%s] tur (akışlı): %s → %s %s (%.2f sn)", self.call_id, log_text(meta["heard"]),
                     log_text(turn.text),
                     [a.type for a in turn.actions], meta["dialog_s"])
            if turn.text:
                self._emit("reply", text=turn.text)
            await self._update_hints()
        return meta["turn"]

    async def _speak_stream(self, chunks: Any, speech_id: int) -> None:
        said_at = self.clock()
        completed, first, tts_ok, heard_any = False, None, True, True
        try:
            res = await self.speaker.say_stream(chunks, self.media)
            first, tts_ok, heard_any = res.first_audio_s, res.ok, res.sentences > 0
            completed = True
        finally:
            with contextlib.suppress(Exception):
                await chunks.aclose()
            self._q.put_nowait(_Event("speech_done", {"speech_id": speech_id, "completed": completed,
                                                      "said_at": said_at, "first_audio_s": first,
                                                      "tts_ok": tts_ok, "heard_any": heard_any}, self.clock()))

    def _start_speech(self, turn: DialogTurn, heard_at: float | None, heard: str, dialog_s: float = 0.0) -> None:
        turn = self._route(turn)
        if self.state != CallState.GREETING:
            self.state = CallState.SPEAKING
        self._speech_id += 1
        self._speech_began = self.clock()
        self._speech_meta = {"id": self._speech_id, "turn": turn, "heard_at": heard_at, "heard": heard,
                             "dialog_s": dialog_s}
        self._speech = asyncio.create_task(self._speak(turn.text, self._speech_id), name=f"konusma-{self.call_id}")
        if turn.text:
            self._emit("reply", text=turn.text)

    async def _speak(self, text: str, speech_id: int) -> None:
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
            self._q.put_nowait(_Event("speech_done", {"speech_id": speech_id, "completed": completed,
                                                      "said_at": said_at, "first_audio_s": first,
                                                      "tts_ok": tts_ok, "heard_any": heard_any}, self.clock()))

    async def _speech_done(self, speech_id: int, completed: bool, said_at: float,
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
            self._emit("turn", **asdict(rec))
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

    async def _apply(self, turn: DialogTurn, completed: bool) -> None:
        if self.state in (CallState.HANDED_OFF, CallState.ENDED):
            return
        op = next((a for a in turn.actions if isinstance(a, OperatorAction)), None)
        tr = next((a for a in turn.actions if isinstance(a, TransferAction)), None)
        hu = next((a for a in turn.actions if isinstance(a, HangupAction)), None)
        if hu is not None:
            await self._send_hangup(hu.reason)
        elif op is not None:
            await self._send_operator(op.reason)
        elif tr is not None and completed:
            await self._send_transfer(tr)
        else:
            if tr is not None:
                log.info("[%s] aktarma cümlesine söze girildi: %s'e aktarma iptal", self.call_id, tr.extension)
                self.dialog.state.pending_transfer = None
            if turn.ended:
                self._handed_off("transferred", "aktarma_tamam")
            else:
                self._listen()

    async def _send_transfer(self, tr: TransferAction) -> None:
        self.state = CallState.TRANSFERRING
        self._emit("action", action="transfer", extension=tr.extension)
        self._transfer_deadline = self.clock() + self.cfg.transfer_result_timeout_s
        try:
            await self.pbx.send_action(self.call_id, {"action_id": _action_id(), "type": "transfer",
                                                      "extension": tr.extension})
        except PbxError as e:
            log.error("[%s] santral aktarma isteğini almadı (%s)", self.call_id, e)
            await self._transfer_result("failed")

    async def _send_operator(self, reason: str) -> None:
        self._emit("action", action="operator", reason=reason)
        self._handed_off("operator", reason)
        try:
            await self.pbx.send_action(self.call_id, {"action_id": _action_id(), "type": "operator"})
        except PbxError as e:
            log.error("[%s] santral operatör isteğini almadı (%s): çağrı kapatılıyor", self.call_id, e)
            self.stats.outcome, self.stats.reason = "error", "santral_eylem_hatasi"
            self._stop.set()

    async def _send_hangup(self, reason: str) -> None:
        self._emit("action", action="hangup", reason=reason)
        self._handed_off("closed", reason)
        try:
            await self.pbx.send_action(self.call_id, {"action_id": _action_id(), "type": "hangup"})
        except PbxError as e:
            log.error("[%s] santral kapatma isteğini almadı (%s)", self.call_id, e)
            self._stop.set()

    async def _transfer_result(self, status: str) -> None:
        if self.state != CallState.TRANSFERRING:
            log.warning("[%s] beklenmeyen aktarma sonucu (%s, durum %s)", self.call_id, status, self.state)
            return
        self._transfer_deadline = None
        turn = await self._thinking(self.dialog.handle_transfer_result(status))
        if status == "answered":
            self._handed_off("transferred", "cevaplandi")
            return
        self.state = CallState.THINKING
        self._start_speech(turn, heard_at=None, heard=f"[santral: {status}]")

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
                self._start_speech(DialogTurn(self.dialog.agent.cfg.texts.no_input, fallback=True),
                                   heard_at=None, heard="[sessizlik]")

    # --- yardımcılar --------------------------------------------------------------------------------------

    def _listen(self) -> None:
        self.state = CallState.LISTENING
        self._listen_since = self.clock()

    def _speaking(self) -> bool:
        return self._speech is not None and not self._speech.done()

    def _handed_off(self, outcome: str, reason: str) -> None:
        self.state = CallState.HANDED_OFF
        self.stats.outcome, self.stats.reason = outcome, reason
        self._inputs.clear()

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

    def _operator_turn(self, reason: str) -> DialogTurn:
        return DialogTurn(self.dialog.agent.cfg.texts.to_operator, [OperatorAction(reason)], fallback=True)

    async def _emergency_operator(self, reason: str) -> None:
        """Hata anonsu (hazır ses; TTS çökmüş olabilir) + operatör."""
        if self._speaking():
            self._speech.cancel()
        self._speech_meta = {}
        text = self.dialog.agent.cfg.texts.error
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self.speaker.say(text, self.media), timeout=15)
        await self._send_operator(reason)


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


def _keyboard():
    global _KEYBOARD
    if _KEYBOARD is None:
        _KEYBOARD = keyboard_sound()
    return _KEYBOARD


def _action_id() -> str:
    return f"a-{secrets.token_hex(4)}"


def _describe(a: Action) -> str:
    if isinstance(a, TransferAction):
        return f"transfer:{a.extension}"
    return f"{a.type}:{a.reason}"
