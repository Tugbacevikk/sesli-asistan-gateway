"""SIPp için konuşmayı taklit eden G.711 A-law RTP kaydı (pcap): 0,3 sn sessizlik + 1,2 sn ton + 1,5 sn sessizlik.

    python tools/santral/sipp/pcap_yap.py            # → tools/santral/sipp/konusma.pcap
    sipp 127.0.0.1:5060 -sf tools/santral/sipp/arayan.xml -m 1 ...   (arayan.xml bu dosyayı oynatır)

Gerçek ses yerine ton: STT'nin konuşma algılaması tetiklenir; tanınan metin STT tarafında belirlenir (deneme).
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
from sesli_asistan.libs.audio import g711  # noqa: E402

RATE = 8000
LOCAL = bytes([127, 0, 0, 1])


def checksum(b: bytes) -> int:
    if len(b) % 2:
        b += b"\0"
    s = sum(struct.unpack(f"!{len(b) // 2}H", b))
    s = (s >> 16) + (s & 0xFFFF)
    s += s >> 16
    return (~s) & 0xFFFF


def main() -> None:
    t = np.arange(int(1.2 * RATE)) / RATE
    x = np.concatenate([np.zeros(int(0.3 * RATE)), 0.3 * np.sin(2 * np.pi * 300 * t), np.zeros(int(1.5 * RATE))])
    payload = g711.encode(x.astype(np.float32), "pcma")
    out = [struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1)]
    n = 0
    for n, k in enumerate(range(0, len(payload), 160)):
        rtp = struct.pack("!BBHII", 0x80, 8 | (0x80 if n == 0 else 0), n, n * 160, 0x1234) + payload[k:k + 160]
        udp = struct.pack("!HHHH", 6000, 6000, 8 + len(rtp), 0) + rtp
        ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(udp), 0, 0, 64, 17, 0, LOCAL, LOCAL)
        ip = ip[:10] + struct.pack("!H", checksum(ip)) + ip[12:]
        frame = b"\0" * 12 + b"\x08\x00" + ip + udp
        us = n * 20000
        out.append(struct.pack("<IIII", us // 1000000, us % 1000000, len(frame), len(frame)) + frame)
    dest = Path(__file__).with_name("konusma.pcap")
    dest.write_bytes(b"".join(out))
    print(f"{dest} ({n + 1} paket)")


if __name__ == "__main__":
    main()
