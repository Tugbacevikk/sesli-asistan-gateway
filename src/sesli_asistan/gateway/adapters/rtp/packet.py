"""RTP paketi (RFC 3550) ayrıştırma ve oluşturma.

Kaynak: Vietnamese-speech-to-text-models `infrastructure/rtp.py` → `parse()` (aynen).
Eklenen: `build()` — Gateway asistan sesini santrale RTP ile gönderir.

Başlık (12 bayt): V=2 | P | X | CC | M | PT | sıra no (16 bit) | zaman damgası (32 bit) | SSRC (32 bit)
ardından CC × 4 bayt CSRC, varsa uzantı başlığı, en sonda varsa dolgu.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass


@dataclass(frozen=True)
class RtpPacket:
    pt: int
    seq: int
    ts: int
    ssrc: int
    payload: bytes
    marker: bool = False


def parse(data: bytes) -> RtpPacket | None:
    """RTP başlığını çözer; RTP değilse (ör. RTCP, bozuk) None."""
    if len(data) < 12 or data[0] >> 6 != 2:
        return None
    b0, b1, seq, ts, ssrc = struct.unpack("!BBHII", data[:12])
    pt = b1 & 0x7F
    if 72 <= pt <= 76:  # RTCP aynı porta düşmüş
        return None
    off = 12 + 4 * (b0 & 0x0F)
    if b0 & 0x10:  # uzantı başlığı
        if len(data) < off + 4:
            return None
        off += 4 + 4 * struct.unpack("!H", data[off + 2:off + 4])[0]
    end = len(data)
    if b0 & 0x20 and end > off:  # dolgu
        end -= data[-1]
    if off > end:
        return None
    return RtpPacket(pt, seq, ts, ssrc, data[off:end], marker=bool(b1 & 0x80))


def build(pt: int, seq: int, ts: int, ssrc: int, payload: bytes, marker: bool = False) -> bytes:
    """Uzantısız, dolgusuz, CSRC'siz RTP paketi."""
    b1 = (pt & 0x7F) | (0x80 if marker else 0)
    return struct.pack("!BBHII", 0x80, b1, seq & 0xFFFF, ts & 0xFFFFFFFF, ssrc & 0xFFFFFFFF) + payload
