"""Gateway → STT servisi: gRPC çift yönlü akış istemcisi (tasarim.md 6.3).

Kullanım (Aşama 5'te CallSession):
    stt = GrpcStt("127.0.0.1:50051")
    stream = await stt.open(call_id, "pcma", hotwords=directory.index.hotwords())
    rtp kareleri → await stream.send(payload)
    async for ev in stream.events(): ...        # speech_started / final
    await stream.close()

Sözleşme kuralları (6.3):
  - Bağlantı kurma zaman aşımı 2 sn; kurulamazsa SttError (çağrı operatöre yönlenir).
  - Akış koparsa (UNAVAILABLE) bir kez yeniden bağlanılır; yine koparsa SttError.
  - Her akışta `x-call-id` meta verisi gönderilir.
  - Gönderim kuyruğu sınırlıdır; STT yetişemezse en eski kare atılır (gecikme birikmesin), sayaç tutulur.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

import grpc

from sesli_asistan.contracts.stt.v1 import stt_pb2, stt_pb2_grpc
from sesli_asistan.gateway.core.ports import SttError, SttEvent

log = logging.getLogger(__name__)

_CODEC = {"pcma": stt_pb2.AUDIO_CODEC_PCMA, "pcmu": stt_pb2.AUDIO_CODEC_PCMU, "pcm16": stt_pb2.AUDIO_CODEC_PCM16}
_TYPE = {stt_pb2.SttEvent.SPEECH_STARTED: "speech_started", stt_pb2.SttEvent.PARTIAL: "partial",
         stt_pb2.SttEvent.FINAL: "final", stt_pb2.SttEvent.NOT_RECOGNIZED: "not_recognized"}
_END = object()


class GrpcStt:
    def __init__(self, target: str = "127.0.0.1:50051", connect_timeout_s: float = 2.0,
                 channel: grpc.aio.Channel | None = None):
        self.target, self.connect_timeout_s = target, connect_timeout_s
        self._channel = channel
        self._stub: stt_pb2_grpc.SpeechToTextStub | None = None

    @property
    def channel(self) -> grpc.aio.Channel:
        # Kanal ilk kullanımda, çalışan olay döngüsünün içinde açılır. grpc.aio kanalı açıldığı döngüye bağlanır:
        # uvicorn başlamadan (döngü yokken) açılırsa "attached to a different loop" hatası verir
        # (Antigravity uçtan uca ölçümü, 27.10.2026).
        if self._channel is None:
            self._channel = grpc.aio.insecure_channel(self.target)
        return self._channel

    @property
    def stub(self) -> stt_pb2_grpc.SpeechToTextStub:
        if self._stub is None:
            self._stub = stt_pb2_grpc.SpeechToTextStub(self.channel)
        return self._stub

    async def ready(self) -> bool:
        """Standart gRPC sağlık kontrolü: model yüklenip ısınmış mı."""
        from grpc_health.v1 import health_pb2, health_pb2_grpc

        try:
            r = await health_pb2_grpc.HealthStub(self.channel).Check(
                health_pb2.HealthCheckRequest(service="stt.v1.SpeechToText"), timeout=self.connect_timeout_s)
            return r.status == health_pb2.HealthCheckResponse.SERVING
        except grpc.aio.AioRpcError:
            return False

    async def open(self, call_id: str, codec: str, hotwords: list[str] | None = None,
                   sample_rate_hz: int = 8000) -> GrpcSttStream:
        if codec not in _CODEC:
            raise ValueError(f"desteklenmeyen codec: {codec}")
        try:
            await asyncio.wait_for(self.channel.channel_ready(), timeout=self.connect_timeout_s)
        except TimeoutError as e:
            raise SttError(f"STT servisine {self.connect_timeout_s} sn içinde bağlanılamadı") from e
        cfg = stt_pb2.StreamConfig(call_id=call_id, codec=_CODEC[codec], sample_rate_hz=sample_rate_hz,
                                   hotwords=hotwords or [])
        s = GrpcSttStream(self.stub, cfg)
        s._start()
        return s

    async def aclose(self) -> None:
        if self._channel is not None:
            await self._channel.close()


class GrpcSttStream:
    def __init__(self, stub: stt_pb2_grpc.SpeechToTextStub, cfg: stt_pb2.StreamConfig, max_queue: int = 250,
                 retries: int = 1):
        self.stub, self.cfg, self.retries = stub, cfg, retries
        self.dropped = 0
        self._q: asyncio.Queue = asyncio.Queue(maxsize=max_queue)
        self._call = None
        self._closed = False

    def _start(self) -> None:
        self._call = self.stub.Stream(self._requests(), metadata=(("x-call-id", self.cfg.call_id),))

    async def _requests(self):
        yield stt_pb2.AudioIn(config=self.cfg)
        while (item := await self._q.get()) is not _END:
            if isinstance(item, list):
                yield stt_pb2.AudioIn(hotwords=stt_pb2.HotwordUpdate(hotwords=item))
            else:
                yield stt_pb2.AudioIn(audio=item)

    async def send(self, payload: bytes) -> None:
        if self._closed:
            return
        await self.send_item(payload)

    async def set_hotwords(self, hotwords: list[str]) -> None:
        """Akış ortasında ipuçlarını değiştir (sonraki cümlelerden itibaren). Yeniden bağlanırsa yenisi kullanılır."""
        if self._closed:
            return
        del self.cfg.hotwords[:]
        self.cfg.hotwords.extend(hotwords)
        await self.send_item(list(hotwords))

    async def send_item(self, item: bytes | list[str]) -> None:
        try:
            self._q.put_nowait(item)
        except asyncio.QueueFull:
            self.dropped += 1
            self._q.get_nowait()
            self._q.put_nowait(item)

    async def events(self) -> AsyncIterator[SttEvent]:
        while True:
            try:
                async for ev in self._call:
                    yield SttEvent(type=_TYPE.get(ev.type, "unknown"), text=ev.text, confidence=ev.confidence,
                                   start_ms=ev.audio_start_ms, end_ms=ev.audio_end_ms, reason=ev.reason)
                return
            except grpc.aio.AioRpcError as e:
                if self._closed:
                    return
                if e.code() == grpc.StatusCode.UNAVAILABLE and self.retries > 0:
                    self.retries -= 1
                    log.warning("[%s] STT akışı koptu (%s); yeniden bağlanılıyor", self.cfg.call_id, e.details())
                    self._q = asyncio.Queue(maxsize=self._q.maxsize)  # eski kareler atılır
                    self._start()
                    continue
                raise SttError(f"STT akışı: {e.code().name} {e.details()}") from e

    async def close(self) -> None:
        """Ses bitti: kalan cümlenin FINAL'ı gelebilir; `events()` sonra biter."""
        if self._closed:
            return
        self._closed = True
        if self._q.full():
            self._q.get_nowait()  # bitiş işareti mutlaka gitsin
        self._q.put_nowait(_END)

    def cancel(self) -> None:
        """Çağrı bitti, sonuç beklenmiyor."""
        self._closed = True
        if self._call is not None:
            self._call.cancel()
