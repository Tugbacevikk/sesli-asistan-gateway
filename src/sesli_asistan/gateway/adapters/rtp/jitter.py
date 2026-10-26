"""Sıra tamponu (jitter buffer).

Kaynak: Vietnamese-speech-to-text-models `infrastructure/rtp.py` → `JitterBuffer` (aynen).

Paketler ağda sırasız gelebilir. Tampon sıra numarasına göre dizer; beklenen paket `depth`
paket boyunca gelmezse kayıp sayar (çıktıda None). Geç gelen (zaten geçilmiş) paket atılır.
16 bit sıra numarasının sarması (65535 → 0) dikkate alınır.
"""
from __future__ import annotations

from .packet import RtpPacket


class JitterBuffer:
    def __init__(self, depth: int = 8):
        self.depth = depth
        self.next: int | None = None
        self.pending: dict[int, RtpPacket] = {}
        self.late = 0  # geç ya da tekrar gelip atılan paket sayısı

    def push(self, p: RtpPacket) -> list[RtpPacket | None]:
        if self.next is None:
            self.next = p.seq
        if (p.seq - self.next) & 0xFFFF >= 0x8000:  # geç kalmış (zaten geçildi): at
            self.late += 1
            return []
        if p.seq in self.pending:  # tekrar
            self.late += 1
            return []
        self.pending[p.seq] = p
        out: list[RtpPacket | None] = []
        while self.pending:
            if self.next in self.pending:
                out.append(self.pending.pop(self.next))
            elif len(self.pending) > self.depth:
                out.append(None)  # bekledik, gelmedi: kayıp
            else:
                break
            self.next = (self.next + 1) & 0xFFFF
        return out

    def drain(self) -> list[RtpPacket | None]:
        out: list[RtpPacket | None] = []
        while self.pending:
            out.append(self.pending.pop(self.next, None))
            self.next = (self.next + 1) & 0xFFFF
        return out
