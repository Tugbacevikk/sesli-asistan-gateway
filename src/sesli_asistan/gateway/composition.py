"""Bileşenleri ayarlardan kurar (bağımlılıkların tek birleştiği yer)."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import httpx

from sesli_asistan import __version__

from .abbreviations import load_abbreviations, load_stt_corrections, load_title_synonyms
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
from .core.people.common import add_title_synonyms
from .core.ports import LlmPort, MediaPort, PbxPort, TtsPort
from .core.schedule import AFTER_HOURS, HOLIDAY, OPEN, OfficeHours
from .core.search import DirectoryIndex
from .core.speech import PromptLibrary, Speaker
from .core.tools import ToolRegistry
from .local_settings import AfterHoursMode

if TYPE_CHECKING:
    import numpy as np
from .core.topics import Topic, parse_topics

log = logging.getLogger(__name__)
_WARMUP_S = 120.0                    # LLM ısıtması en çok (model diskten yükleniyorsa ~60 sn)


def build_llm(s: GatewaySettings) -> LlmPort:
    # HTTP süre sınırı iki çağrı türünün uzun olanı: anlama çağrısı (ADR-037) daha uzun bekleyebilir
    timeout = max(s.llm_timeout_s, s.understand_timeout_s)
    if s.llm_backend == "ollama":
        return OllamaChat(s.llm_base_url, s.llm_model, s.llm_temperature, timeout)
    return OpenAiCompatChat(s.llm_base_url, s.llm_model, s.llm_temperature, timeout)


def build_directory(s: GatewaySettings) -> DirectoryCache:
    if s.directory_source != "json":
        raise NotImplementedError("Sentra Rehber API adaptörü Aşama 9'da (API yazılınca)")
    index = DirectoryIndex(min_score=s.match_min_score, high_score=s.match_high_score, margin=s.match_margin)
    return DirectoryCache(JsonDirectory(s.directory_path), index, s.directory_refresh_s)


def load_prompt(path: str) -> str:
    """Sistem istemi şablonu (LLM_PROMPT_PATH); boşsa "" (koddaki istem). Yer tutucu yalnız {kurum}: başka süslü
    parantez varsa servis açılmaz (çağrı sırasında değil, açılışta hata)."""
    if not path.strip():
        return ""
    text = Path(path).read_text(encoding="utf-8").strip()
    try:
        text.format(kurum="")
    except (KeyError, IndexError, ValueError) as e:
        raise ValueError(f"LLM_PROMPT_PATH ({path}): yalnız {{kurum}} yer tutucusu kullanılabilir ({e!r})") from e
    if not text:
        raise ValueError(f"LLM_PROMPT_PATH ({path}) boş")
    return text


def build_agent(s: GatewaySettings, directory: DirectoryCache, llm: LlmPort | None = None) -> DialogAgent:
    cfg = DialogConfig(organization=s.organization_name, max_failed_attempts=s.max_failed_attempts,
                       max_tool_rounds=s.max_tool_rounds, max_history=s.max_history,
                       llm_timeout_s=s.llm_timeout_s, texts=s.dialog_texts(), fast_path=s.fast_path,
                       clarify_by_code=s.clarify_by_code, stream_replies=s.stream_replies,
                       confirm_below=s.confirm_below, repeat_below=s.repeat_below,
                       code_replies=s.code_replies, llm_enabled=s.llm_enabled, llm_fallback=s.llm_fallback,
                       mcp_step=s.mcp_step, topic_confirm=s.topic_confirm, office_hours=s.office().describe(),
                       prompt_template=load_prompt(s.llm_prompt_path),
                       prompt_departments=s.llm_prompt_departments, few_shot=s.llm_few_shot,
                       small_talk=s.small_talk, understand=s.understand_mode,
                       understand_timeout_s=s.understand_timeout_s,
                       stt_corrections=load_stt_corrections(s.abbreviations_path.strip()))
    remote = None
    if s.llm_tools == "mcp" or s.mcp_step:
        from .adapters.mcp.rehber import McpRehber

        # ADR-022: LLM'in rehber araması (LLM_TOOLS=mcp); ADR-023: LLM'den önce konu adımı (MCP_STEP)
        remote = McpRehber(s.mcp_url)
    tools = ToolRegistry(directory.index, remote=remote, remote_timeout_s=s.mcp_timeout_s,
                         remote_search=s.llm_tools == "mcp", topics=load_topics(s.mcp_topics_path.strip()))
    return DialogAgent(llm or build_llm(s), tools, cfg)


def load_topics(path: str = "") -> list[Topic]:
    """Konu listesi: `path` (kurumun listesi, MCP_TOPICS_PATH), boşsa ürünle gelen genel liste (gateway/konular.json).
    Gateway ve rehber MCP sunucusu aynı listeyi okur."""
    from importlib import resources

    if path:
        text = Path(path).read_text(encoding="utf-8")
    else:
        text = resources.files("sesli_asistan.gateway").joinpath("konular.json").read_text(encoding="utf-8")
    return parse_topics(json.loads(text))


def build_tts(s: GatewaySettings) -> CachingTts:
    piper = PiperHttpTts(s.tts_url, s.tts_path, s.tts_format, s.tts_voice, s.tts_length_scale, s.tts_timeout_s)
    return CachingTts(piper, max_items=s.tts_cache_items, disk_dir=s.tts_cache_dir)


async def build_speaker(s: GatewaySettings, agent: DialogAgent,
                        tts: TtsPort | None = None) -> tuple[Speaker, list[str]]:
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
        from .live import LiveFeed

        self.live = LiveFeed(keep_texts=s.call_history_texts)    # İzleme: süren çağrıların canlı akışı
        self.directory = build_directory(s)
        self.abbreviations = load_abbreviations(s.abbreviations_path.strip(), extra=True)   # rehber yüklemede (ADR-030)
        add_title_synonyms(load_title_synonyms(s.abbreviations_path.strip()))   # kurumun unvan eş anlamları
        from .core.resilience import BreakerLlm, BreakerStt, CircuitBreaker

        self.breakers = {n: CircuitBreaker(n, s.breaker_failures, s.breaker_reset_s) for n in ("llm", "tts", "stt")}
        self.llm = BreakerLlm(TimedLlm(llm or build_llm(s), self.metrics), self.breakers["llm"])
        self.agent = build_agent(s, self.directory, self.llm)
        self.tts = tts or build_tts(s)
        self.stt = BreakerStt(stt or GrpcStt(s.stt_grpc_addr), self.breakers["stt"])
        self.pbx = pbx or PbxRestClient(s.pbx_base_url, s.pbx_api_key.get_secret_value(), s.pbx_timeout_s)
        self.speaker: Speaker | None = None
        self.components = {"stt": "starting", "tts": "starting", "directory": "starting"}
        self.mcp = self.agent.tools.remote           # LLM_TOOLS=mcp ise rehber MCP istemcisi (isteğe bağlı bileşen)
        if self.mcp is not None:
            self.components["mcp"] = "starting"
        if s.llm_enabled:                  # LLM kapalıysa (ADR-019) sağlıkta hiç yer almaz
            self.components["llm"] = "starting"
        self.health_interval_s = health_interval_s
        from .adapters.history.sqlite import MemoryCallHistory, SqliteCallHistory

        self.manager = CallManager(media_factory or self._open_media, self._new_session,
                                   PortPool(s.rtp_port_min, s.rtp_port_max), s.max_concurrent_calls,
                                   ready=lambda: self.ready, on_finished=self._record)
        self.history = (SqliteCallHistory(s.call_history_db, s.call_history_days, s.call_history_texts)
                        if s.call_history_db else MemoryCallHistory(self.manager))
        self.prompts_missing = False             # sabit anonslardan biri seslendirilemedi (TTS "down" sayılır)
        self.thinking_pcm = load_thinking_sound(s.thinking_sound_file) if s.thinking_sound_file else None
        self.office = s.office()
        self.after_hours_mode = s.after_hours_mode
        self.top_extensions: list[str] = []      # çağrı geçmişinden en çok aranan dahililer (ipucu önceliği)
        self._tz = ZoneInfo(s.timezone.strip()) if s.timezone.strip() else None
        from .adapters.directory.json_file_store import JsonDirectoryFile
        from .adapters.settings.json_store import LocalSettingsStore
        from .directory_upload import DirectoryUpload
        from .settings_service import SettingsService

        upload = DirectoryUpload(self, JsonDirectoryFile(s.directory_path) if s.directory_source == "json" else None)
        self.settings = SettingsService(self, LocalSettingsStore(s.local_settings_file)
                                        if s.local_settings_file else None, upload)
        self.settings.load()        # anonslar seslendirilmeden önce (start)
        self._health_task: asyncio.Task | None = None
        self._last_purge = time.monotonic()
        from . import call_logs

        self.call_logs = call_logs.attach(s.call_log_dir)     # her konuşmanın log dosyası (CALL_LOG_DIR)

    @property
    def ready(self) -> bool:
        """Çağrı kabul edilir mi. LLM zorunlu değil (ADR-020): düşerse diyalog kural tabanlı sürer; yalnız STT, TTS
        ve rehber zorunlu."""
        comps = self.effective_components()
        # MCP zorunlu değil (ADR-022): ulaşılamazsa LLM'in rehber araması Gateway içinde yapılır
        required = {k: v for k, v in comps.items() if k != "mcp" and (k != "llm" or not self.s.llm_fallback)}
        return self.speaker is not None and all(v == "ok" for v in required.values())

    def effective_components(self) -> dict[str, str]:
        """Bileşen durumu + devre kesici: devresi açık servis "devre_acik" sayılır (yeni çağrı alınmaz)."""
        out = dict(self.components)
        for name, b in self.breakers.items():
            if b.is_open:
                out[name] = "devre_acik"
        return out

    def set_office_rules(self, office: OfficeHours, after_hours_mode: AfterHoursMode) -> None:
        """Ayar ekranından: mesai kuralı ve mesai dışı davranış yeni çağrılarda geçerli."""
        self.office, self.after_hours_mode = office, after_hours_mode

    async def reload_prompts(self) -> list[str]:
        """Sabit anonsları (değişen metinler) yeniden seslendirir; seslendirilemeyenleri döner."""
        if self.speaker is None:
            return []
        missing = await self.speaker.library.preload(self.speaker.tts, self.agent.fixed_prompts())
        self.prompts_missing = bool(missing)
        return missing

    def office_status(self) -> str:
        """acik | mesai_disi | tatil (tasarim.md 5.1.3)."""
        return self.office.status(datetime.now(self._tz))

    def health(self) -> dict:
        comps = self.effective_components()
        status = "draining" if self.manager.draining else "ok" if self.ready else "degraded"
        mode = "kural" if not self.s.llm_enabled or comps.get("llm", "ok") != "ok" else "llm"
        # `mode`: LLM kullanılabiliyor mu. `anlama`: çözülemeyen sözün LLM'le anlaşılıp anlaşılmadığı (ADR-037)
        anlama = self.s.understand_mode if mode == "llm" else "kural"
        return {"status": status, "version": __version__, "mode": mode, "anlama": anlama, "components": comps,
                "active_calls": self.manager.active, "capacity": self.s.max_concurrent_calls,
                "office": self.office_status()}

    async def _open_media(self, port: int, req: NewCall) -> MediaPort:
        return await RtpMedia.open(self.s.rtp_host, port, req.codec, req.rtp_remote, self.s.allowed_ips)

    def _speaker(self) -> Speaker:
        if self.speaker is None:                     # ready() açılış bitmeden çağrı kabul etmez
            raise RuntimeError("konuşmacı hazır değil")
        return self.speaker

    def _new_session(self, req: NewCall, media: MediaPort) -> CallSession:
        return self._session(req.call_id, req.codec, media, self.pbx, self._call_config(), "santral")

    def _session(self, call_id: str, codec: str, media: MediaPort, pbx: PbxPort, cfg: CallConfig, channel: str,
                 observer: Observer | None = None, office: str | None = None) -> CallSession:
        """Santral ve arayüz çağrısı aynı oturumu kullanır; yalnız ses yolu, santral ve gözlemci farklıdır. Olaylar
        metriğe ve canlı izlemeye (ve verilmişse arayüze) gider."""
        observers = [self.metrics.observer(channel), self.live.observer(call_id, channel)]
        return CallSession(call_id, codec, media, self.stt, self._speaker(), self.agent.new_session(call_id), pbx,
                           cfg, hotwords=self._hotwords(), hotword_priority=self.top_extensions,
                           thinking_pcm=self.thinking_pcm, channel=channel,
                           observer=_fanout(*observers, *([observer] if observer else [])),
                           office=office or self.office_status())

    def _call_config(self) -> CallConfig:
        return CallConfig(rtp_idle_timeout_s=self.s.rtp_idle_timeout_s, max_call_duration_s=self.s.max_call_duration_s,
                          transfer_result_timeout_s=self.s.transfer_result_timeout_s, barge_in=self.s.barge_in,
                          background_margin_db=self.s.background_margin_db,
                          no_input_s=self.s.no_input_s, no_input_max=self.s.no_input_max,
                          thinking_sound=self.s.thinking_sound, thinking_sound_delay_s=self.s.thinking_sound_delay_s,
                          after_hours_mode=self.after_hours_mode)

    def web_session(self, call_id: str, media: MediaPort, pbx: PbxPort, observer: Observer, text_mode: bool = False,
                    office: str | None = None, codec: str = "g722") -> CallSession:
        """Arayüzden deneme çağrısı: santral çağrısıyla aynı oturum, ses WebSocket'ten, eylemler arayüze.
        text_mode: arayan yazarak konuşur; sessizlik uyarısı kapalı (yazmak konuşmaktan uzun sürer).
        office: mesai durumunu denemek için zorla (acik | mesai_disi | tatil); None: saate göre.
        codec: g722 (16 kHz geniş bant, varsayılan) ya da pcma (8 kHz G.711), `api/web_call.WsMedia` ile aynı."""
        cfg = self._call_config()
        if text_mode:
            cfg = replace(cfg, no_input_s=cfg.max_call_duration_s * 2)
        return self._session(call_id, codec, media, pbx, cfg, "web-metin" if text_mode else "web", observer,
                             office if office in (OPEN, AFTER_HOURS, HOLIDAY) else None)

    def _hotwords(self) -> list[str]:
        return self.directory.index.hotwords(priority=self.top_extensions)

    async def _refresh_top(self) -> None:
        try:
            rows = await asyncio.to_thread(self.history.top_extensions, 50)
            self.top_extensions = [ext for ext, _ in rows]
        except Exception as e:
            log.warning("çağrı geçmişinden sık aranan dahililer okunamadı: %s", e)

    async def _record(self, call_id: str, stats) -> None:
        if self.call_logs is not None:
            try:
                await asyncio.to_thread(self.call_logs.flush_call, call_id, stats)
            except OSError as e:          # disk dolu / yetki: konuşma geçmişi yine yazılır
                log.error("[%s] çağrı kaydı dosyası yazılamadı: %s", call_id, e)
        await asyncio.to_thread(self.history.add, call_id, stats)

    async def start(self) -> None:
        await asyncio.to_thread(self.history.purge)
        await self._refresh_top()
        await self.directory.refresh()
        self.directory.start()
        from .core.resilience import BreakerTts
        from .metrics import TimedTts

        self.speaker, missing = await build_speaker(
            self.s, self.agent, BreakerTts(TimedTts(self.tts, self.metrics), self.breakers["tts"]))
        self.prompts_missing = bool(missing)
        if missing:
            log.error("sabit anonslar seslendirilemedi: %s (TTS: %s)", missing, self.s.tts_url)
        if self.s.llm_enabled:
            await warm_llm(self.llm, self.agent, self.s.llm_model)
        if self.mcp is not None:
            # bağlantı açılışta kurulur: ilk bağlantı ~1 sn sürüyor, bir çağrının ilk aramasına denk gelmesin
            log.info("rehber MCP: %s (%s)", self.s.mcp_url,
                     "bağlı" if await self.mcp.ping() else "ulaşılamıyor; aramalar yerel yapılacak")
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
                    await self._refresh_top()
                    if self.call_logs is not None:
                        await asyncio.to_thread(self.call_logs.purge, self.s.call_history_days)
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
        if self.s.llm_enabled:
            self.components["llm"] = await _http_up(self.s.llm_base_url) if hasattr(self.llm, "http") else "ok"
        if self.mcp is not None:
            self.components["mcp"] = "ok" if await self.mcp.ping() else "down"
        if self.prompts_missing or self.speaker is None:
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
        from . import call_logs

        call_logs.detach(self.call_logs)
        await self.manager.shutdown()
        self.history.close()
        await self.directory.stop()
        for c in (self.stt, self.pbx, self.llm, self.mcp):
            close = getattr(c, "aclose", None)
            if close:
                await close()


Observer = Callable[[str, dict[str, Any]], None]


async def warm_llm(llm: Any, agent: DialogAgent, model: str) -> None:
    """LLM'i açılışta belleğe alır: ilk çağrının ilk cevabı model yüklenmesini beklemesin. Gerçek çağrıyla aynı baş
    (sistem istemi + araçlar): Ollama önceki isteğin hesabını yeniden kullanır. Isıtma olmazsa servis yine açılır."""
    warm = getattr(llm, "warmup", None)
    if warm is None:
        return
    head = [{"role": "system", "content": agent.system_prompt}, {"role": "user", "content": "merhaba"}]
    t0 = time.monotonic()
    try:
        # süre sınırı: model diskten yüklenirken ısıtma ~60 sn sürebiliyor (ölçüm 06.11.2026, 4 GB GPU)
        try:
            await asyncio.wait_for(warm(messages=head, tools=agent.tools.schemas), timeout=_WARMUP_S)
        except TypeError:                   # isteğe bağlı parametreleri almayan adaptör
            await asyncio.wait_for(warm(), timeout=_WARMUP_S)
        log.info("LLM ısıtıldı: %s (%.1f sn)", model, time.monotonic() - t0)
    except Exception as e:
        log.error("LLM ısıtılamadı: %s", e or type(e).__name__)


def _fanout(*observers: Observer) -> Observer:
    def on(kind: str, data: dict[str, Any]) -> None:
        for o in observers:
            o(kind, data)
    return on


def load_thinking_sound(path: str, level: float = 0.12) -> np.ndarray:
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
