"""Asistan sesini santrale RTP ile gönderir (Gateway → santral). Yeni kod.

Davranış (tasarim.md 6.1):
  - 20 ms'lik G.711 paketleri, **mutlak zaman çizelgesine göre** tempolu gönderilir
    (t0 + i × 20 ms). Göreli `sleep(0.02)` kullanılmaz; o, her pakette biriken sapma üretir.
  - Konuşma (talkspurt) başındaki ilk pakette RTP marker biti 1'dir.
  - SSRC, sıra numarası ve zaman damgası çağrı başına rastgele başlar (RFC 3550).
  - Zaman damgası konuşmalar arasında da ilerler; sessiz aradaki süre eklenir.
  - `play()` iptal edilebilir: arayan söze girince (SPEECH_STARTED) görev iptal edilir ve
    gönderim o pakette durur.
  - Paketler, santralin sesi aldığı porttan (simetrik RTP) gönderilebilsin diye alıcının
    transport'u kullanılabilir.
"""
from __future__ import annotations

import asyncio
import secrets
import time

import numpy as np

from sesli_asistan.libs.audio import g711

from .packet import build

SAMPLE_RATE = 8000
PTIME_MS = 20
SAMPLES_PER_PACKET = SAMPLE_RATE * PTIME_MS // 1000  # 160


class RtpSender:
    def __init__(self, transport: asyncio.DatagramTransport, remote: tuple[str, int], codec: str = "pcma"):
        if codec not in g711.CODECS:
            raise ValueError(f"desteklenmeyen codec: {codec}")
        self.transport, self.remote, self.codec = transport, remote, codec
        self.pt = g711.PAYLOAD_TYPE[codec]
        self.ssrc = secrets.randbits(32)
        self.seq = secrets.randbits(16)
        self.ts = secrets.randbits(32)
        self.sent = 0
        self._last_end: float | None = None  # son paketin bittiği an (monotonic)

    async def play(self, pcm8k: np.ndarray) -> int:
        """8 kHz int16 PCM'i gönderir; gönderilen paket sayısını döner. İptal edilebilir."""
        if pcm8k.dtype != np.int16:
            raise TypeError("pcm8k int16 olmalı (8 kHz)")
        n = -(-len(pcm8k) // SAMPLES_PER_PACKET)
        if n == 0:
            return 0
        pad = np.zeros(n * SAMPLES_PER_PACKET - len(pcm8k), np.int16)
        payload = g711.encode_int16(np.concatenate([pcm8k, pad]), self.codec)

        loop = asyncio.get_running_loop()
        now = time.monotonic()
        if self._last_end is not None and now > self._last_end:
            # sessiz ara: zaman damgası gerçek zamanla birlikte ilerler
            self.ts = (self.ts + int((now - self._last_end) * SAMPLE_RATE)) & 0xFFFFFFFF
        t0 = loop.time()
        sent = 0
        try:
            for i in range(n):
                delay = t0 + i * PTIME_MS / 1000 - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                chunk = payload[i * SAMPLES_PER_PACKET:(i + 1) * SAMPLES_PER_PACKET]
                self.transport.sendto(build(self.pt, self.seq, self.ts, self.ssrc, chunk, marker=(i == 0)),
                                      self.remote)
                self.seq = (self.seq + 1) & 0xFFFF
                self.ts = (self.ts + SAMPLES_PER_PACKET) & 0xFFFFFFFF
                sent += 1
        finally:
            self.sent += sent
            self._last_end = time.monotonic()
        return sent
