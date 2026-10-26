"""STT servisini başlatır: model yükleme, ısınma, sağlık durumu, gRPC sunucusu (tasarim.md 5.2).

    python -m sesli_asistan.stt                       # ayarlar ortamdan / /etc/sesli-asistan/stt.env
    STT_DEVICE=cpu STT_PORT=50051 python -m sesli_asistan.stt

Sağlık: standart gRPC sağlık protokolü (`grpc.health.v1.Health`). Model yüklenip ısınana kadar
"stt.v1.SpeechToText" NOT_SERVING'dir; Gateway ve systemd buna bakar (tasarim.md 9.1: modeller ısınmadan
çağrı kabul edilmez). Kapanışta (SIGTERM) açık akışlara 5 sn süre tanınır.
"""
from __future__ import annotations

import asyncio
import logging
import signal

import grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc

from sesli_asistan.contracts.stt.v1 import stt_pb2_grpc

from .config import SttSettings
from .engine import WhisperEngine, WhisperParams
from .service import Engine, SpeechToTextService
from .vad import SegmenterConfig, make_detector

log = logging.getLogger(__name__)
SERVICE_NAME = "stt.v1.SpeechToText"


def build_service(s: SttSettings, engine: Engine | None) -> SpeechToTextService:
    seg = SegmenterConfig(end_silence_ms=s.endpoint_silence_ms, max_utterance_sec=s.max_utterance_s)
    return SpeechToTextService(engine, lambda: make_detector(s.vad, s.vad_model, s.vad_threshold), seg,
                               max_streams=s.max_streams, timeout_s=s.timeout_s)


async def start_server(s: SttSettings, engine: Engine | None = None, bind: str | None = None):
    """Sunucuyu başlatır; (server, health_servicer, port) döner. engine verilmezse Whisper yüklenip ısıtılır.

    gRPC işleyicileri sunucu başlamadan kaydedilmelidir; bu yüzden servis motorsuz kaydedilir, motor hazır
    olunca bağlanır. Bu arada gelen akışlar UNAVAILABLE ile reddedilir, sağlık durumu NOT_SERVING'dir."""
    server = grpc.aio.server()
    health_svc = health.aio.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_svc, server)
    service = build_service(s, None)
    stt_pb2_grpc.add_SpeechToTextServicer_to_server(service, server)
    for name in ("", SERVICE_NAME):
        await health_svc.set(name, health_pb2.HealthCheckResponse.NOT_SERVING)
    port = server.add_insecure_port(bind or f"{s.host}:{s.port}")
    await server.start()
    log.info("STT gRPC dinliyor: %s (hazırlanıyor)", bind or f"{s.host}:{port}")

    try:
        if engine is None:
            params = WhisperParams(model_dir=s.model_dir, device=s.device, compute_type=s.compute_type,
                                   beam_size=s.beam, num_workers=s.num_workers, cpu_threads=s.cpu_threads)
            engine = await asyncio.to_thread(WhisperEngine, params)
            await asyncio.to_thread(engine.warmup)
            log.info("Whisper hazır (%s)", getattr(engine, "device", "?"))
    except BaseException:
        await server.stop(0)
        raise
    service.engine = engine
    for name in ("", SERVICE_NAME):
        await health_svc.set(name, health_pb2.HealthCheckResponse.SERVING)
    log.info("STT hazır")
    return server, health_svc, port


async def serve(s: SttSettings | None = None) -> None:
    s = s or SttSettings()
    try:
        server, _, _ = await start_server(s)
    except (FileNotFoundError, RuntimeError) as e:   # model yok / CUDA kütüphanesi yok (device=cuda)
        log.error("STT başlatılamadı: %s", e)
        raise SystemExit(1) from None
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows
            pass
    try:
        await stop.wait()
    finally:
        log.info("STT kapanıyor")
        await server.stop(grace=5)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass
