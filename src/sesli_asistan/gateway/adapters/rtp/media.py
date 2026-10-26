"""Bir çağrının RTP ses yolu: `RtpReceiver` (giriş) + `RtpSender` (çıkış) → çekirdeğin `MediaPort`'u.

Çıkış, giriş soketinden gönderilir (simetrik RTP: santral sesi hangi porta gönderiyorsa cevabı da o porttan
alır; NAT/güvenlik duvarında tek port yeter). Hedef, `POST /v1/calls` içindeki `rtp_remote`; verilmediyse
gelen ilk paketin kaynak adresi (bu durumda ilk paket gelene kadar çalınan ses beklenir).
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import time

import numpy as np

from .receiver import RtpReceiver
from .sender import RtpSender

log = logging.getLogger(__name__)


class RtpMedia:
    def __init__(self, receiver: RtpReceiver, remote: tuple[str, int] | None, codec: str,
                 wait_remote_s: float = 2.0):
        self.rx, self.remote, self.codec, self.wait_remote_s = receiver, remote, codec, wait_remote_s
        self.opened_at = time.monotonic()
        self._tx: RtpSender | None = None

    @classmethod
    async def open(cls, host: str, port: int, codec: str, remote: tuple[str, int] | None,
                   allowed_ips: set[str] | None) -> RtpMedia:
        rx = RtpReceiver(codec=codec, allowed_ips=allowed_ips)
        await rx.open(host, port)          # OSError: port kullanımda → CallManager sıradakini dener
        return cls(rx, remote, codec)

    async def frames(self):
        while (f := await self.rx.frames.get()) is not None:
            yield f

    async def play(self, pcm8k: np.ndarray) -> int:
        tx = await self._sender()
        if tx is None:
            log.warning("RTP hedefi bilinmiyor (rtp_remote yok, paket de gelmedi): ses çalınamadı")
            return 0
        return await tx.play(pcm8k)

    async def _sender(self) -> RtpSender | None:
        if self._tx is None:
            remote = self.remote
            deadline = time.monotonic() + self.wait_remote_s
            while remote is None and time.monotonic() < deadline:
                remote = self.rx.remote
                if remote is None:
                    await asyncio.sleep(0.02)
            if remote is None or self.rx.transport is None:
                return None
            self._tx = RtpSender(self.rx.transport, remote, self.codec)
        return self._tx

    def idle_s(self) -> float:
        last = self.rx.last_packet_at or self.opened_at
        return time.monotonic() - last

    def stats(self) -> dict[str, int]:
        d = dataclasses.asdict(self.rx.stats)
        d["gonderilen"] = self._tx.sent if self._tx else 0
        return d

    def close(self) -> None:
        self.rx.close()
