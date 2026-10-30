"""Sıra tamponu (jitter buffer).

Kaynak: Vietnamese-speech-to-text-models `infrastructure/rtp.py` → `JitterBuffer` (aynen).

Paketler ağda sırasız gelebilir. Tampon sıra numarasına göre dizer; beklenen paket `depth`
paket boyunca gelmezse kayıp sayar (çıktıda None). Geç gelen (zaten geçilmiş) paket atılır.
16 bit sıra numarasının sarması (65535 → 0) dikkate alınır.
"""
from __future__ import annotations

from .packet import RtpPacket

MAX_FILL = 25          # en çok bu kadar kayıp paket (0,5 sn) sessizlikle doldurulur; fazlası sıra atlaması


class JitterBuffer:
    def __init__(self, depth: int = 8):
        self.depth = depth
        self.next: int | None = None
        self.pending: dict[int, RtpPacket] = {}
        self.late = 0  # geç ya da tekrar gelip atılan paket sayısı
        self.resyncs = 0

    def push(self, p: RtpPacket) -> list[RtpPacket | None]:
        if self.next is None:
            self.next = p.seq
        nxt: int = self.next
        if (p.seq - self.next) & 0xFFFF >= 0x8000:  # geç kalmış (zaten geçildi): at
            self.late += 1
            return []
        if p.seq in self.pending:  # tekrar
            self.late += 1
            return []
        self.pending[p.seq] = p
        out: list[RtpPacket | None] = []
        while self.pending:
            if nxt in self.pending:
                out.append(self.pending.pop(nxt))
            elif len(self.pending) > self.depth:
                nearest = min(self.pending, key=lambda s: (s - nxt) & 0xFFFF)
                if (nearest - nxt) & 0xFFFF > MAX_FILL:
                    # büyük sıra atlaması (bekletme, santral akışı yeniden başlattı): saniyelerce sahte sessizlik
                    # üretilmez, akış yeni sıradan sürer
                    self.resyncs += 1
                    nxt = nearest
                    continue
                out.append(None)  # bekledik, gelmedi: kayıp
            else:
                break
            nxt = (nxt + 1) & 0xFFFF
        self.next = nxt
        return out

    def drain(self) -> list[RtpPacket | None]:
        """Kalanlar sırayla; küçük boşluklar sessizlikle, büyük sıra atlamaları doldurulmadan."""
        out: list[RtpPacket | None] = []
        if self.next is None:
            return out
        nxt = self.next
        while self.pending:
            if nxt in self.pending:
                out.append(self.pending.pop(nxt))
            else:
                nearest = min(self.pending, key=lambda s: (s - nxt) & 0xFFFF)
                gap = (nearest - nxt) & 0xFFFF
                if gap > MAX_FILL:
                    nxt = nearest
                    continue
                out.extend([None] * gap)
                nxt = nearest
                continue
            nxt = (nxt + 1) & 0xFFFF
        self.next = nxt
        return out
