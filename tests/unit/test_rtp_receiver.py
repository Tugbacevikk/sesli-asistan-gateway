"""Kaynak sistemdeki dersin testi: tüketici yavaşken de soket boşaltılır, paket kaybolmaz.

Kaynak: Vietnamese-speech-to-text-models `tests/unit/test_rtp_reader.py` (asyncio alıcıya uyarlandı).
"""
import asyncio
import socket
import struct

import numpy as np

from sesli_asistan.gateway.adapters.rtp import RtpReceiver, RtpSender, parse
from sesli_asistan.libs.audio import g711


def _pkt(seq: int, ssrc: int = 0xABCD, pt: int = 8) -> bytes:
    return struct.pack("!BBHII", 0x80, pt, seq, seq * 160, ssrc) + b"\xd5" * 160  # PCMA, 20 ms


async def _collect(rx: RtpReceiver) -> list[bytes]:
    out = []
    while not rx.frames.empty():
        out.append(rx.frames.get_nowait())
    return out


async def test_yavas_tuketici_paket_kaybettirmez():
    rx = RtpReceiver("pcma", allowed_ips={"127.0.0.1"})
    port = await rx.open("127.0.0.1", 0)
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    n = 400
    for i in range(n):
        tx.sendto(_pkt(i), ("127.0.0.1", port))
        if i % 50 == 0:
            await asyncio.sleep(0)  # döngü okumaya devam etsin; tüketici hiç okumuyor
    await asyncio.sleep(0.3)
    rx.close()
    frames = await _collect(rx)
    payloads = [f for f in frames if f is not None]
    assert rx.stats.kayip == 0
    assert rx.stats.paket == n
    # sıra tamponunun son birkaç paketi close() ile boşaltılır
    assert len(payloads) == n


async def test_kayip_sessizlikle_dolar_ve_yabanci_ssrc_atilir():
    rx = RtpReceiver("pcma", jitter_depth=2)
    port = await rx.open("127.0.0.1", 0)
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    for s in [0, 1, 3, 4, 5, 6]:  # 2 kayıp
        tx.sendto(_pkt(s), ("127.0.0.1", port))
    tx.sendto(_pkt(7, ssrc=0x9999), ("127.0.0.1", port))  # başka akış
    tx.sendto(_pkt(8, pt=101), ("127.0.0.1", port))       # DTMF (telephone-event)
    await asyncio.sleep(0.2)
    rx.close()
    payloads = [f for f in await _collect(rx) if f is not None]
    assert rx.stats.kayip == 1
    assert rx.stats.yabanci_ssrc == 1
    assert rx.stats.desteklenmeyen == 1
    assert len(payloads) == 7
    silence = g711.encode_int16(np.zeros(160, np.int16), "pcma")
    assert payloads[2] == silence


async def test_izin_listesi_disindaki_ip_reddedilir():
    rx = RtpReceiver("pcma", allowed_ips={"10.0.0.5"})
    port = await rx.open("127.0.0.1", 0)
    socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(_pkt(0), ("127.0.0.1", port))
    await asyncio.sleep(0.1)
    rx.close()
    assert rx.stats.yabanci_ip == 1 and rx.stats.paket == 0


class _Collector(asyncio.DatagramProtocol):
    def __init__(self):
        self.items = []

    def datagram_received(self, data, addr):
        self.items.append((asyncio.get_running_loop().time(), parse(data)))


async def test_gonderici_tempolu_ve_iptal_edilebilir():
    loop = asyncio.get_running_loop()
    sink_t, sink = await loop.create_datagram_endpoint(_Collector, local_addr=("127.0.0.1", 0))
    src_t, _ = await loop.create_datagram_endpoint(asyncio.DatagramProtocol, local_addr=("127.0.0.1", 0))
    tx = RtpSender(src_t, sink_t.get_extra_info("sockname"), "pcma")

    sent = await tx.play(np.zeros(160 * 10, np.int16))  # 10 paket = 200 ms
    await asyncio.sleep(0.05)
    assert sent == 10 and len(sink.items) == 10
    times = [t for t, _ in sink.items]
    assert 0.15 <= times[-1] - times[0] <= 0.30
    pkts = [p for _, p in sink.items]
    assert pkts[0].marker and not any(p.marker for p in pkts[1:])
    assert all((b.seq - a.seq) & 0xFFFF == 1 for a, b in zip(pkts, pkts[1:], strict=False))
    assert all(p.pt == 8 and len(p.payload) == 160 for p in pkts)

    # barge-in: uzun ses başlatılıp iptal edilir
    task = asyncio.create_task(tx.play(np.zeros(160 * 100, np.int16)))
    await asyncio.sleep(0.1)
    task.cancel()
    await asyncio.sleep(0.1)
    assert 10 < len(sink.items) < 10 + 20
    sink_t.close()
    src_t.close()
