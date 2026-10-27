"""gRPC `stt.v1.SpeechToText/Stream` uygulaması (tasarim.md 5.2, 6.3, ADR-004).

Her çağrı = bir çift yönlü akış:
    Gateway → AudioIn(config) + AudioIn(audio) × N      (20 ms G.711 kareleri)
    STT     → SttEvent(SPEECH_STARTED) … SttEvent(FINAL | NOT_RECOGNIZED) …

Akış içinde iki görev çalışır ve birbirini **beklemez**:
  1. okuyucu : kareleri alır, G.711 çözer, 8→16 kHz, VAD/cümle bölücü; SPEECH_STARTED'ı hemen gönderir,
               biten cümleyi kuyruğa koyar.
  2. çözücü  : kuyruktaki cümleleri sırayla Whisper'a verir (ayrı iş parçacığında), FINAL'ı gönderir.
Whisper bir cümleyi çözerken okuyucu ses almaya devam eder. Kaynak sistemdeki ders: okuma ile tanıma
aynı akışta olunca ses kaybediliyordu (tasarim.md 6.1).

Hatalı istekler gRPC durum kodlarıyla reddedilir: ilk mesaj config değilse / codec ya da hız geçersizse
INVALID_ARGUMENT; eşzamanlı akış sınırı doluysa RESOURCE_EXHAUSTED.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import wave
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Protocol

import grpc
import numpy as np

from sesli_asistan.contracts.stt.v1 import stt_pb2, stt_pb2_grpc
from sesli_asistan.libs.audio import g711
from sesli_asistan.libs.audio.resample import StreamUpsampler

from .engine import SttResult
from .vad import Segmenter, SegmenterConfig, SpeechDetector, SpeechStarted, Utterance

log = logging.getLogger(__name__)


class _Metrics:
    """STT metrikleri (prometheus-client kuruluysa; yoksa hiçbir şey yapmaz). `STT_METRICS_PORT`'tan yayınlanır."""

    def __init__(self):
        try:
            from prometheus_client import Counter, Gauge, Histogram
        except ImportError:
            self.ok = False
            return
        self.ok = True
        b = (0.1, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0)
        self.decode = Histogram("stt_decode_seconds", "Cümle çözüm süresi (Whisper)", buckets=b)
        self.audio = Histogram("stt_utterance_seconds", "Cümle ses uzunluğu", buckets=(0.5, 1, 2, 3, 5, 8, 12))
        self.results = Counter("stt_results_total", "Cümle sonuçları", ["result"])
        self.active = Gauge("stt_streams_active", "Açık akış")

    def result(self, kind: str, decode_s: float, audio_s: float) -> None:
        if self.ok:
            self.results.labels(kind).inc()
            self.audio.observe(audio_s)
            if kind != "timeout":
                self.decode.observe(decode_s)

    def streams(self, n: int) -> None:
        if self.ok:
            self.active.set(n)


_M = _Metrics()

STT_RATE = 16000
_CODECS = {stt_pb2.AUDIO_CODEC_PCMA: "pcma", stt_pb2.AUDIO_CODEC_PCMU: "pcmu", stt_pb2.AUDIO_CODEC_PCM16: "pcm16"}


class Engine(Protocol):
    def transcribe(self, samples: np.ndarray, hotwords: list[str] | None = None) -> SttResult: ...


class SpeechToTextService(stt_pb2_grpc.SpeechToTextServicer):
    def __init__(self, engine: Engine | None, detector_factory: Callable[[], SpeechDetector],
                 segmenter_config: SegmenterConfig | None = None, max_streams: int = 64, timeout_s: float = 3.0,
                 debug_dir: str | None = None):
        self.engine, self.detector_factory = engine, detector_factory
        self.seg_cfg = segmenter_config or SegmenterConfig()
        self.max_streams, self.timeout_s = max_streams, timeout_s
        self.active = 0
        # Teşhis: her cümlenin Whisper'a giden sesi + sonucu diske yazılır (STT_DEBUG_DIR). Ses kaydı tutulduğu için
        # üretimde KAPALI (tasarim.md 10.4); yalnızca deneme ortamında açılır.
        self.debug_dir = Path(debug_dir) if debug_dir else None
        if self.debug_dir:
            self.debug_dir.mkdir(parents=True, exist_ok=True)
            log.warning("STT teşhis kaydı AÇIK: cümle sesleri %s klasörüne yazılıyor", self.debug_dir)

    async def Stream(self, requests: AsyncIterator[stt_pb2.AudioIn], context) -> AsyncIterator[stt_pb2.SttEvent]:  # noqa: N802
        if self.engine is None:
            await context.abort(grpc.StatusCode.UNAVAILABLE, "STT hazırlanıyor (model yükleniyor)")
        if self.active >= self.max_streams:
            await context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, f"eşzamanlı akış sınırı ({self.max_streams})")
        self.active += 1
        _M.streams(self.active)
        tasks: list[asyncio.Task] = []
        try:
            try:
                first = await anext(aiter(requests))
            except StopAsyncIteration:
                return
            cfg = first.config if first.WhichOneof("msg") == "config" else None
            if cfg is None:
                await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "ilk mesaj StreamConfig olmalı")
            codec = _CODECS.get(cfg.codec)
            rate = cfg.sample_rate_hz
            if codec is None or (codec != "pcm16" and rate != 8000) or rate not in (8000, 16000):
                await context.abort(grpc.StatusCode.INVALID_ARGUMENT,
                                    f"desteklenmeyen biçim: codec={cfg.codec} hız={rate} (G.711 yalnızca 8000 Hz)")
            call_id = cfg.call_id or dict(context.invocation_metadata()).get("x-call-id", "?")
            hints = {"words": list(cfg.hotwords)}    # akış ortasında HotwordUpdate ile değişir
            log.info("[%s] STT akışı açıldı: %s %d Hz, %d ipucu", call_id, codec, rate, len(hints["words"]))

            out: asyncio.Queue[stt_pb2.SttEvent | None] = asyncio.Queue()
            utts: asyncio.Queue[Utterance | None] = asyncio.Queue()
            tasks = [asyncio.create_task(self._read(requests, codec, rate, out, utts, hints, call_id),
                                         name=f"stt-oku-{call_id}"),
                     asyncio.create_task(self._decode(call_id, hints, utts, out), name=f"stt-coz-{call_id}")]
            while (ev := await out.get()) is not None:
                yield ev
            log.info("[%s] STT akışı kapandı", call_id)
        finally:
            for t in tasks:
                t.cancel()
            for t in tasks:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t
            self.active -= 1
            _M.streams(self.active)

    async def _read(self, requests, codec: str, rate: int, out: asyncio.Queue, utts: asyncio.Queue,
                    hints: dict | None = None, call_id: str = "?") -> None:
        seg = Segmenter(self.detector_factory(), self.seg_cfg)
        up = StreamUpsampler(rate, STT_RATE) if rate != STT_RATE else None
        try:
            async for msg in requests:
                kind = msg.WhichOneof("msg")
                if kind == "hotwords" and hints is not None:
                    hints["words"] = list(msg.hotwords.hotwords)
                    log.info("[%s] ipuçları güncellendi: %d öğe", call_id, len(hints["words"]))
                    continue
                if kind != "audio" or not msg.audio:
                    continue
                x = (np.frombuffer(msg.audio, "<i2").astype(np.float32) / 32768.0 if codec == "pcm16"
                     else g711.decode(msg.audio, codec))
                if up is not None:
                    x = up.process(x)
                for e in seg.feed(x):
                    if isinstance(e, SpeechStarted):
                        out.put_nowait(stt_pb2.SttEvent(type=stt_pb2.SttEvent.SPEECH_STARTED,
                                                        audio_start_ms=int(e.start * 1000)))
                    else:
                        utts.put_nowait(e)
            for u in seg.flush():
                utts.put_nowait(u)
        finally:
            utts.put_nowait(None)

    async def _decode(self, call_id: str, hints: dict, utts: asyncio.Queue, out: asyncio.Queue) -> None:
        orphan: asyncio.Future | None = None
        n = 0
        try:
            while (u := await utts.get()) is not None:
                n += 1
                if orphan is not None:
                    # zaman aşımına uğrayan çözüm hâlâ motoru tutuyor: bitmesini bekle; yoksa sıradaki cümlenin
                    # süresi motor boşalmadan başlar ve o da haksız yere zaman aşımına düşer
                    with contextlib.suppress(Exception):
                        await orphan
                    orphan = None
                t0 = time.perf_counter()
                reason = ""
                hotwords = hints["words"]
                job = asyncio.ensure_future(asyncio.to_thread(self.engine.transcribe, u.audio, hotwords or None))
                try:
                    r = await asyncio.wait_for(asyncio.shield(job), timeout=self.timeout_s)
                except TimeoutError:
                    # iş parçacığı durdurulamaz: çözüm arka planda biter, sonucu atılır
                    log.warning("[%s] STT zaman aşımı (%.1f sn ses, sınır %.1f sn)", call_id, u.end - u.start,
                                self.timeout_s)
                    orphan = job
                    reason = "timeout"
                except Exception:
                    log.exception("[%s] STT hatası", call_id)
                    reason = "error"
                else:
                    if not r.text:
                        log.info("[%s] boş/uydurma metin atıldı (%.2f sn)", call_id, time.perf_counter() - t0)
                        reason = "empty"
                if self.debug_dir:
                    self._dump(call_id, n, u, hotwords, "" if reason else r.text, reason)
                _M.result(reason or "final", time.perf_counter() - t0, u.end - u.start)
                if reason:
                    # arayan konuştu ama metin yok: Gateway sessiz kalmasın, tekrar sorsun (tasarim.md 6.3)
                    out.put_nowait(stt_pb2.SttEvent(type=stt_pb2.SttEvent.NOT_RECOGNIZED, reason=reason,
                                                    audio_start_ms=int(u.start * 1000),
                                                    audio_end_ms=int(u.end * 1000)))
                    continue
                dt = time.perf_counter() - t0
                log.info("[%s] FINAL %.2f sn ses → %.2f sn: %s", call_id, u.end - u.start, dt, r.text)
                out.put_nowait(stt_pb2.SttEvent(type=stt_pb2.SttEvent.FINAL, text=r.text, confidence=r.confidence,
                                                audio_start_ms=int(u.start * 1000), audio_end_ms=int(u.end * 1000)))
        finally:
            out.put_nowait(None)

    def _dump(self, call_id: str, n: int, u: Utterance, hotwords: list[str], text: str, reason: str) -> None:
        try:
            base = self.debug_dir / f"{call_id}_{n:03d}"
            pcm = (np.clip(u.audio, -1, 1) * 32767).astype("<i2")
            with wave.open(str(base.with_suffix(".wav")), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(STT_RATE)
                w.writeframes(pcm.tobytes())
            base.with_suffix(".json").write_text(json.dumps(
                {"call_id": call_id, "cumle": n, "bas_sn": u.start, "son_sn": u.end, "metin": text, "neden": reason,
                 "ipuclari": hotwords}, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError as e:
            log.warning("teşhis kaydı yazılamadı: %s", e)
