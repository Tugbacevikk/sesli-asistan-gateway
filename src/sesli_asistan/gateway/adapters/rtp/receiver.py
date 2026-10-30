"""Bir çağrının RTP giriş portu (santral → Gateway).

Kaynak: Vietnamese-speech-to-text-models `infrastructure/rtp.py` → `RtpSource`. Korunan davranış:
  - Soket, sesi işleyen koddan bağımsız, sürekli boşaltılır; alma tamponu büyütülür (1 MiB).
    Kaynak sistemde okuma ile tanıma aynı iş parçacığındayken çekirdek tamponu dolup %4–24
    paket düştü (23 Ekim 2026, kök neden: RcvbufErrors). Burada okuma asyncio olay döngüsünde,
    `datagram_received` yalnızca ayrıştırıp kuyruğa koyar; ağır iş (STT) ayrı süreçtedir.
  - Sıra tamponu, kayıp paketin yerine aynı uzunlukta sessizlik (zaman çizgisi kaymaz).
  - İlk gelen SSRC'ye kilitlenme; başka akışların paketleri atılır.
Uyarlanan:
  - Asistan için çağrı başına port (ADR-002); iş parçacığı yerine asyncio (çok sayıda eşzamanlı çağrı).
  - Kaynak IP izin listesi (tasarim.md 10.1, sahte RTP).
  - Çıktı çözülmez: G.711 baytları olduğu gibi STT servisine gider (stt.v1.AudioIn.audio).
Alınmayan: SDP / dinamik yük türü / L16 (santral G.711 gönderir), ffmpeg kaynağı.
"""
from __future__ import annotations

import asyncio
import logging
import socket
import time
from dataclasses import dataclass, field

import numpy as np

from sesli_asistan.libs.audio import g711

from .jitter import JitterBuffer
from .packet import RtpPacket, parse

log = logging.getLogger(__name__)

RCVBUF_BYTES = 1 << 20
SSRC_SWITCH_S = 0.3        # eski akış bu kadar sessizse yeni SSRC kabul edilir


@dataclass
class RtpStats:
    paket: int = 0             # kabul edilen
    kayip: int = 0             # sıra boşluğundan (sessizlikle dolduruldu)
    gec_tekrar: int = 0        # geç ya da tekrar gelip atılan
    yabanci_ssrc: int = 0
    yabanci_ip: int = 0
    desteklenmeyen: int = 0    # G.711 dışı yük (ör. telephone-event / DTMF)
    kuyruk_tasmasi: int = 0
    kuyrukta_en_cok: int = field(default=0)
    ssrc_degisimi: int = 0


class RtpReceiver(asyncio.DatagramProtocol):
    """Kullanım:

        rx = RtpReceiver(codec="pcma", allowed_ips={"10.0.0.5"})
        port = await rx.open("0.0.0.0", 10012)
        while (payload := await rx.frames.get()) is not None:
            ...   # 20 ms G.711 baytları, sırayla, kayıplar sessizlikle dolu
        rx.close()
    """

    def __init__(self, codec: str = "pcma", allowed_ips: set[str] | None = None,
                 jitter_depth: int = 8, max_queue: int = 500):
        if codec not in g711.CODECS:
            raise ValueError(f"desteklenmeyen codec: {codec}")
        self.codec = codec
        self.pt = g711.PAYLOAD_TYPE[codec]
        self.allowed_ips = allowed_ips
        self.jitter = JitterBuffer(jitter_depth)
        self.frames: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=max_queue)
        self.stats = RtpStats()
        self.ssrc: int | None = None
        self.last_packet_at: float | None = None   # time.monotonic(); boşta kalma zaman aşımı için
        self.transport: asyncio.DatagramTransport | None = None
        self.remote: tuple[str, int] | None = None  # paketin geldiği adres (simetrik RTP için)
        self._frame_len = 160  # 20 ms @ 8 kHz; ilk paketten güncellenir
        self._silence = g711.encode_int16(np.zeros(self._frame_len, np.int16), codec)

    async def open(self, host: str, port: int) -> int:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, RCVBUF_BYTES)
        sock.bind((host, port))
        sock.setblocking(False)
        loop = asyncio.get_running_loop()
        await loop.create_datagram_endpoint(lambda: self, sock=sock)
        return sock.getsockname()[1]

    @property
    def port(self) -> int | None:
        return self.transport.get_extra_info("sockname")[1] if self.transport else None

    def close(self) -> None:
        for p in self.jitter.drain():
            self._emit(p)
        if self.transport:
            self.transport.close()

    # asyncio.DatagramProtocol
    def connection_made(self, transport) -> None:
        self.transport = transport

    def connection_lost(self, exc) -> None:
        self._put(None)  # tüketiciye "akış bitti"

    def datagram_received(self, data: bytes, addr) -> None:
        # Burada yalnızca hafif iş yapılır (ayrıştırma + kuyruk); asla bekleyen/ağır çağrı yok.
        if self.allowed_ips is not None and addr[0] not in self.allowed_ips:
            self.stats.yabanci_ip += 1
            return
        p = parse(data)
        if p is None:
            return
        if p.pt != self.pt:
            self.stats.desteklenmeyen += 1
            return
        now = time.monotonic()
        if self.ssrc is None:
            self.ssrc, self.remote = p.ssrc, addr
            log.info("RTP akışı: ssrc=%08x codec=%s kaynak=%s:%s", p.ssrc, self.codec, *addr)
        elif p.ssrc != self.ssrc:
            # Santral çağrı ortasında SSRC değiştirebilir (köprü değişimi, bekletme müziği, yeniden yönlendirme).
            # Eski akış SSRC_SWITCH_S boyunca susmuşsa ve yeni paket aynı adresten geliyorsa yeni akışa geçilir;
            # aksi hâlde (iki akış aynı anda) yabancı sayılır.
            silent = self.last_packet_at is None or now - self.last_packet_at >= SSRC_SWITCH_S
            if not (silent and addr == self.remote):
                self.stats.yabanci_ssrc += 1
                return
            log.warning("RTP SSRC değişti: %08x → %08x (eski akış %.1f sn sessiz)", self.ssrc, p.ssrc,
                        now - (self.last_packet_at or now))
            for q in self.jitter.drain():
                self._emit(q)
            self.jitter = JitterBuffer(self.jitter.depth)
            self.ssrc = p.ssrc
            self.stats.ssrc_degisimi += 1
        self.last_packet_at = now
        late_before = self.jitter.late
        out = self.jitter.push(p)
        self.stats.gec_tekrar += self.jitter.late - late_before
        if self.jitter.late == late_before:
            self.stats.paket += 1
        for q in out:
            self._emit(q)

    def _emit(self, p: RtpPacket | None) -> None:
        if p is None:
            self.stats.kayip += 1
            payload = self._silence
        else:
            payload = p.payload
            if len(payload) != self._frame_len:
                self._frame_len = len(payload)
                self._silence = g711.encode_int16(np.zeros(self._frame_len, np.int16), self.codec)
        self._put(payload)

    def _put(self, item: bytes | None) -> None:
        try:
            self.frames.put_nowait(item)
        except asyncio.QueueFull:
            # Tüketici (STT akışı) tıkanmışsa en eski kare atılır: canlı seste gecikme birikmesin.
            self.stats.kuyruk_tasmasi += 1
            self.frames.get_nowait()
            self.frames.put_nowait(item)
        self.stats.kuyrukta_en_cok = max(self.stats.kuyrukta_en_cok, self.frames.qsize())
