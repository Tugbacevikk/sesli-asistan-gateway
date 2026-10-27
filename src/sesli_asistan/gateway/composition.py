"""Bileşenleri ayarlardan kurar (bağımlılıkların tek birleştiği yer)."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import replace

import httpx

from .adapters.directory.json_dir import JsonDirectory
from .adapters.llm.ollama import OllamaChat
from .adapters.llm.openai_compat import OpenAiCompatChat
from .adapters.pbx.rest import PbxRestClient
from .adapters.rtp.media import RtpMedia
from .adapters.stt.grpc_client import GrpcStt
from .adapters.tts.cache import CachingTts
from .adapters.tts.piper_http import PiperHttpTts
from .config import GatewaySettings
from .core.call import CallConfig, CallSession
from .core.dialog import DialogAgent, DialogConfig
from .core.directory import DirectoryCache
from .core.manager import CallManager, NewCall, PortPool
from .core.ports import LlmPort, MediaPort
from .core.search import DirectoryIndex
from .core.speech import PromptLibrary, Speaker
from .core.tools import ToolRegistry

log = logging.getLogger(__name__)


def build_llm(s: GatewaySettings) -> LlmPort:
    if s.llm_backend == "ollama":
        return OllamaChat(s.llm_base_url, s.llm_model, s.llm_temperature, s.llm_timeout_s)
    return OpenAiCompatChat(s.llm_base_url, s.llm_model, s.llm_temperature, s.llm_timeout_s)


def build_directory(s: GatewaySettings) -> DirectoryCache:
    if s.directory_source != "json":
        raise NotImplementedError("Sentra Rehber API adaptörü Aşama 9'da (API yazılınca)")
    index = DirectoryIndex(min_score=s.match_min_score, high_score=s.match_high_score, margin=s.match_margin)
    return DirectoryCache(JsonDirectory(s.directory_path), index, s.directory_refresh_s)


def build_agent(s: GatewaySettings, directory: DirectoryCache, llm: LlmPort | None = None) -> DialogAgent:
    cfg = DialogConfig(organization=s.organization_name, max_failed_attempts=s.max_failed_attempts,
                       max_tool_rounds=s.max_tool_rounds, max_history=s.max_history,
                       llm_timeout_s=s.llm_timeout_s, texts=s.dialog_texts(), fast_path=s.fast_path)
    return DialogAgent(llm or build_llm(s), ToolRegistry(directory.index), cfg)


def build_tts(s: GatewaySettings) -> CachingTts:
    piper = PiperHttpTts(s.tts_url, s.tts_path, s.tts_format, s.tts_voice, s.tts_length_scale, s.tts_timeout_s)
    return CachingTts(piper, max_items=s.tts_cache_items, disk_dir=s.tts_cache_dir)


async def build_speaker(s: GatewaySettings, agent: DialogAgent,
                        tts: CachingTts | None = None) -> tuple[Speaker, list[str]]:
    """Konuşmacıyı kurar ve sabit anonsları önceden seslendirir. Dönen liste: seslendirilemeyen anonslar
    (boş değilse servis "hazır değil" sayılmalı; tasarim.md 9.1)."""
    tts = tts or build_tts(s)
    library = PromptLibrary()
    missing = await library.preload(tts, agent.fixed_prompts())
    return Speaker(tts, library, timeout_s=s.tts_timeout_s), missing


# --- Aşama 5: çalışan servis ---------------------------------------------------------------------------------

class GatewayRuntime:
    """Gateway servisinin bütün parçaları ve yaşam döngüsü (açılış, sağlık, kapanış).

    Açılış sırası (tasarim.md 9.1 "modeller henüz ısınmadı"): rehber yüklenir, sabit anonslar seslendirilir,
    LLM belleğe alınır; bunlar ve STT hazır olana kadar `/v1/health` 503 döner ve yeni çağrı reddedilir.
    Sağlık arka planda `health_interval_s`'de bir kontrol edilir (her istekte değil).
    Testler sahte LLM / STT / santral / TTS / medya verebilir.
    """

    def __init__(self, s: GatewaySettings, llm: LlmPort | None = None, stt=None, pbx=None, tts=None,
                 media_factory=None, health_interval_s: float = 10.0):
        from .metrics import GatewayMetrics, TimedLlm

        self.s = s
        self.metrics = GatewayMetrics(self)
        self.directory = build_directory(s)
        from .core.resilience import BreakerLlm, BreakerStt, CircuitBreaker

        self.breakers = {n: CircuitBreaker(n, s.breaker_failures, s.breaker_reset_s) for n in ("llm", "tts", "stt")}
        self.llm = BreakerLlm(TimedLlm(llm or build_llm(s), self.metrics), self.breakers["llm"])
        self.agent = build_agent(s, self.directory, self.llm)
        self.tts = tts or build_tts(s)
        self.stt = BreakerStt(stt or GrpcStt(s.stt_grpc_addr), self.breakers["stt"])
        self.pbx = pbx or PbxRestClient(s.pbx_base_url, s.pbx_api_key.get_secret_value(), s.pbx_timeout_s)
        self.speaker: Speaker | None = None
        self.components = {"stt": "starting", "llm": "starting", "tts": "starting", "directory": "starting"}
        self.health_interval_s = health_interval_s
        from .adapters.history.sqlite import MemoryCallHistory, SqliteCallHistory

        self.manager = CallManager(media_factory or self._open_media, self._new_session,
                                   PortPool(s.rtp_port_min, s.rtp_port_max), s.max_concurrent_calls,
                                   ready=lambda: self.ready, on_finished=self._record)
        self.history = (SqliteCallHistory(s.call_history_db, s.call_history_days, s.call_history_texts)
                        if s.call_history_db else MemoryCallHistory(self.manager))
        self._prompts_missing = False
        self.thinking_pcm = load_thinking_sound(s.thinking_sound_file) if s.thinking_sound_file else None
        self._health_task: asyncio.Task | None = None
        self._last_purge = time.monotonic()

    @property
    def ready(self) -> bool:
        return self.speaker is not None and all(v == "ok" for v in self.effective_components().values())

    def effective_components(self) -> dict[str, str]:
        """Bileşen durumu + devre kesici: devresi açık servis "devre_acik" sayılır (yeni çağrı alınmaz)."""
        out = dict(self.components)
        for name, b in self.breakers.items():
            if b.is_open:
                out[name] = "devre_acik"
        return out

    def health(self) -> dict:
        return {"status": "ok" if self.ready else "degraded", "components": self.effective_components(),
                "active_calls": self.manager.active, "capacity": self.s.max_concurrent_calls}

    async def _open_media(self, port: int, req: NewCall) -> MediaPort:
        return await RtpMedia.open(self.s.rtp_host, port, req.codec, req.rtp_remote, self.s.allowed_ips)

    def _new_session(self, req: NewCall, media: MediaPort) -> CallSession:
        return CallSession(req.call_id, req.codec, media, self.stt, self.speaker, self.agent.new_session(req.call_id),
                           self.pbx, self._call_config(), hotwords=self.directory.index.hotwords(),
                           thinking_pcm=self.thinking_pcm, observer=self.metrics.observer("santral"))

    def _call_config(self) -> CallConfig:
        return CallConfig(rtp_idle_timeout_s=self.s.rtp_idle_timeout_s, max_call_duration_s=self.s.max_call_duration_s,
                          transfer_result_timeout_s=self.s.transfer_result_timeout_s, barge_in=self.s.barge_in,
                          no_input_s=self.s.no_input_s, no_input_max=self.s.no_input_max,
                          thinking_sound=self.s.thinking_sound, thinking_sound_delay_s=self.s.thinking_sound_delay_s)

    def web_session(self, call_id: str, media: MediaPort, pbx, observer, text_mode: bool = False) -> CallSession:
        """Arayüzden deneme çağrısı: santral çağrısıyla aynı oturum, ses WebSocket'ten, eylemler arayüze.
        text_mode: arayan yazarak konuşur; sessizlik uyarısı kapalı (yazmak konuşmaktan uzun sürer)."""
        cfg = self._call_config()
        channel = "web-metin" if text_mode else "web"
        stats = self.metrics.observer(channel)

        def both(kind, data):
            observer(kind, data)
            stats(kind, data)

        if text_mode:
            cfg = replace(cfg, no_input_s=cfg.max_call_duration_s * 2)
        return CallSession(call_id, "pcma", media, self.stt, self.speaker, self.agent.new_session(call_id), pbx,
                           cfg, hotwords=self.directory.index.hotwords(), observer=both,
                           thinking_pcm=self.thinking_pcm, channel=channel)

    async def _record(self, call_id: str, stats) -> None:
        await asyncio.to_thread(self.history.add, call_id, stats)

    async def start(self) -> None:
        await asyncio.to_thread(self.history.purge)
        await self.directory.refresh()
        self.directory.start()
        from .core.resilience import BreakerTts
        from .metrics import TimedTts

        self.speaker, missing = await build_speaker(
            self.s, self.agent, BreakerTts(TimedTts(self.tts, self.metrics), self.breakers["tts"]))
        self._prompts_missing = bool(missing)
        if missing:
            log.error("sabit anonslar seslendirilemedi: %s (TTS: %s)", missing, self.s.tts_url)
        warm = getattr(self.llm, "warmup", None)
        if warm:
            try:
                await asyncio.wait_for(warm(), timeout=60)
            except Exception as e:
                log.error("LLM ısıtılamadı: %s", e)
        await self.check_health()
        self._health_task = asyncio.create_task(self._health_loop(), name="saglik")
        log.info("Gateway %s: %s", "hazır" if self.ready else "HAZIR DEĞİL", self.components)

    async def _health_loop(self) -> None:
        while True:
            await asyncio.sleep(self.health_interval_s)
            try:
                await self.check_health()
                if time.monotonic() - self._last_purge > 3600:
                    self._last_purge = time.monotonic()
                    await asyncio.to_thread(self.history.purge)
            except Exception:
                log.exception("sağlık kontrolü hatası")

    async def check_health(self) -> None:
        before = dict(self.components)
        self.components["directory"] = "ok" if self.directory.ready else "down"
        stt_ready = getattr(self.stt, "ready", None)
        try:
            self.components["stt"] = "ok" if stt_ready is None or await stt_ready() else "down"
        except Exception as e:   # sağlık kontrolü servisi düşürmez; bileşen "down" sayılır
            log.error("STT sağlık kontrolü hatası: %s", e)
            self.components["stt"] = "down"
        self.components["llm"] = await _http_up(self.s.llm_base_url) if hasattr(self.llm, "http") else "ok"
        if self._prompts_missing or self.speaker is None:
            self.components["tts"] = "down"
        elif isinstance(self.tts, CachingTts):
            self.components["tts"] = await _http_up(self.s.tts_url)
        else:
            self.components["tts"] = "ok"
        if self.components != before:
            log.info("bileşen durumu: %s", self.components)

    async def stop(self) -> None:
        if self._health_task:
            self._health_task.cancel()
        await self.manager.shutdown()
        self.history.close()
        await self.directory.stop()
        for c in (self.stt, self.pbx, self.llm):
            close = getattr(c, "aclose", None)
            if close:
                await close()


def load_thinking_sound(path: str, level: float = 0.12):
    """Düşünme sesi dosyası → 8 kHz mono int16, tepe `level`'a ayarlı (konuşmanın altında kalsın).
    Okunamazsa servis başlamaz (yanlış yol sessizce kodla üretilen sese düşmesin)."""
    import wave

    import numpy as np

    from sesli_asistan.libs.audio.resample import resample

    with wave.open(path, "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError(f"THINKING_SOUND_FILE 16 bit PCM WAV olmalı: {path}")
        x = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768
        x = x.reshape(-1, w.getnchannels()).mean(axis=1)
        x = resample(x, w.getframerate(), 8000)
    peak = float(np.abs(x).max()) or 1.0
    log.info("düşünme sesi: %s (%.1f sn)", path, len(x) / 8000)
    return (x / peak * level * 32767).astype(np.int16)


async def _http_up(url: str) -> str:
    """Servis HTTP'ye cevap veriyor mu (durum kodu önemsiz: 404 de "ayakta" demektir)."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            await c.get(url)
        return "ok"
    except httpx.HTTPError:
        return "down"
