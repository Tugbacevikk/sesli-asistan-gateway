"""G.722 geniş bant (ADR-027): santral ↔ Gateway; STT'ye 16 kHz PCM16 gider."""
import asyncio
import math
import socket
import struct

import numpy as np
import pytest

pytest.importorskip("G722")

from sesli_asistan.gateway.adapters.rtp import RtpReceiver, RtpSender, parse  # noqa: E402
from sesli_asistan.libs.audio import g722  # noqa: E402


def _tone(n: int, rate: int, hz: float = 3000.0) -> np.ndarray:
    """3 kHz: G.711'in (4 kHz sınırı) taşıyabildiği ama G.722'nin rahat taşıdığı ses; 6 kHz: yalnız G.722."""
    return np.array([int(8000 * math.sin(2 * math.pi * hz * i / rate)) for i in range(n)], np.int16)


def test_kodla_coz_ve_6khz_sesi_tasir():
    x = _tone(16000, 16000, hz=6000)        # "s" / "ş" bölgesi: 8 kHz G.711'de hiç yoktur
    enc, dec = g722.Encoder(), g722.Decoder()
    payload = [enc.encode(x[i:i + 320]) for i in range(0, len(x), 320)]
    assert {len(p) for p in payload} == {160}                       # 20 ms = 160 bayt
    y = np.frombuffer(b"".join(dec.decode(p) for p in payload), np.int16)
    assert len(y) == len(x)
    spec = np.abs(np.fft.rfft(y[4000:12000].astype(float)))
    freqs = np.fft.rfftfreq(8000, 1 / 16000)
    assert abs(freqs[spec.argmax()] - 6000) < 20


def _pkt(seq: int, payload: bytes, ssrc: int = 0xABCD) -> bytes:
    return struct.pack("!BBHII", 0x80, 9, seq, seq * 160, ssrc) + payload


async def test_alici_cozer_kayipta_16khz_sessizlik():
    enc = g722.Encoder()
    frames = [enc.encode(_tone(320, 16000)) for _ in range(7)]
    rx = RtpReceiver("g722", jitter_depth=2)
    port = await rx.open("127.0.0.1", 0)
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    for s in [0, 1, 3, 4, 5, 6]:                                    # 2 kayıp
        tx.sendto(_pkt(s, frames[s]), ("127.0.0.1", port))
    tx.sendto(struct.pack("!BBHII", 0x80, 8, 7, 0, 0xABCD) + b"\xd5" * 160, ("127.0.0.1", port))   # PCMA: başka
    await asyncio.sleep(0.2)
    rx.close()
    out = []
    while not rx.frames.empty():
        out.append(rx.frames.get_nowait())
    out = [f for f in out if f is not None]
    assert len(out) == 7 and {len(f) for f in out} == {640}       # 320 örnek × 2 bayt PCM16
    assert out[2] == bytes(640) and rx.stats.kayip == 1 and rx.stats.desteklenmeyen == 1
    assert np.abs(np.frombuffer(out[0], np.int16)).max() > 1000


class _Collector(asyncio.DatagramProtocol):
    def __init__(self):
        self.items = []

    def datagram_received(self, data, addr):
        self.items.append(parse(data))


async def test_gonderici_g722_paketleri_ve_zaman_damgasi():
    loop = asyncio.get_running_loop()
    sink_t, sink = await loop.create_datagram_endpoint(_Collector, local_addr=("127.0.0.1", 0))
    src_t, _ = await loop.create_datagram_endpoint(asyncio.DatagramProtocol, local_addr=("127.0.0.1", 0))
    tx = RtpSender(src_t, sink_t.get_extra_info("sockname"), "g722")
    sent = await tx.play(_tone(160 * 10, 8000, hz=1000))          # asistan sesi çekirdekte 8 kHz
    await asyncio.sleep(0.05)
    pkts = sink.items
    assert sent == 10 and len(pkts) == 10
    assert all(p.pt == 9 and len(p.payload) == 160 for p in pkts)
    assert all((b.ts - a.ts) & 0xFFFFFFFF == 160 for a, b in zip(pkts, pkts[1:], strict=False))   # RTP saati 8000
    dec = g722.Decoder()
    y = np.frombuffer(b"".join(dec.decode(p.payload) for p in pkts), np.int16)
    spec = np.abs(np.fft.rfft(y[800:2400].astype(float)))
    assert abs(np.fft.rfftfreq(1600, 1 / 16000)[spec.argmax()] - 1000) < 20
    sink_t.close()
    src_t.close()


def test_stt_ye_16khz_pcm_olarak_gider():
    from sesli_asistan.gateway.adapters.stt.grpc_client import stream_format

    assert stream_format("g722") == ("pcm16", 16000)
    assert stream_format("pcma") == ("pcma", 8000) and stream_format("pcmu") == ("pcmu", 8000)


def test_yonetici_ve_api_g722_kabul_eder():
    from sesli_asistan.gateway.api.app import CreateCall
    from sesli_asistan.gateway.core.manager import CODECS

    assert "g722" in CODECS
    assert CreateCall.model_fields["codec"].annotation.__args__ == ("pcma", "pcmu", "g722")
