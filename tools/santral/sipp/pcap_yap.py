"""SIPp için arayanın sesini taklit eden G.711 A-law RTP kaydı (pcap).

    python tools/santral/sipp/pcap_yap.py            # → konusma.pcap: 0,3 sn sessizlik + 1,2 sn ton + 1,5 sn sessizlik
    sipp 127.0.0.1:5060 -sf tools/santral/sipp/arayan.xml -m 1 ...   (arayan.xml bu dosyayı oynatır)

Zincir denemesi (mikrofonsuz arayan): gerçek konuşma kayıtları zaman çizgisinde istenen saniyelere konur.
    python tools/tts/seslendir.py --metin "Ayşe hanım" --metin "Ayşe Yılmaz" --cikti ses_ornekleri
    python tools/santral/sipp/pcap_yap.py --ses ses_ornekleri/1.wav@5.2 --ses ses_ornekleri/2.wav@10.8 \\
        --sure 30 --cikti tools/santral/sipp/zincir.pcap
    cd tools/santral/sipp && sipp 127.0.0.1:5060 -sf arayan_zincir.xml -s 500 -au 1001 -ap ŞİFRE -m 1 -mi 127.0.0.1
Saniyeler çağrının cevaplandığı andan itibarendir. STT teşhis kaydındaki çağrı sesi (`<çağrı>_akis.wav`,
tools/stt/vad_incele.py) aynı saniyelerde bu sesleri tam içermelidir; eksikse ses santral / köprü / Gateway
yolunda kayboluyor demektir (02.11.2026 santral denemesi: asistanın sözünün hemen ardından gelen cevaplar).

Gerçek ses yerine ton (varsayılan): STT'nin konuşma algılaması tetiklenir; tanınan metin STT tarafında belirlenir.
"""
from __future__ import annotations

import argparse
import struct
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
from sesli_asistan.libs.audio import g711  # noqa: E402
from sesli_asistan.libs.audio.resample import resample  # noqa: E402

RATE = 8000
LOCAL = bytes([127, 0, 0, 1])


def checksum(b: bytes) -> int:
    if len(b) % 2:
        b += b"\0"
    s = sum(struct.unpack(f"!{len(b) // 2}H", b))
    s = (s >> 16) + (s & 0xFFFF)
    s += s >> 16
    return (~s) & 0xFFFF


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        if w.getsampwidth() != 2:
            raise SystemExit(f"{path}: yalnız 16 bit PCM")
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768
        if w.getnchannels() > 1:
            x = x.reshape(-1, w.getnchannels()).mean(axis=1)
        rate = w.getframerate()
    return resample(x, rate, RATE) if rate != RATE else x


def timeline(clips: list[tuple[np.ndarray, float]], total_s: float) -> np.ndarray:
    """Sessiz zaman çizgisine sesler yerleştirilir (üst üste binerse toplanır, kırpılır)."""
    end = max([total_s, *(at + len(x) / RATE for x, at in clips)])
    out = np.zeros(int(end * RATE) + 1, dtype=np.float32)
    for x, at in clips:
        i = int(at * RATE)
        out[i:i + len(x)] += x
    return np.clip(out, -1, 1)


def tone() -> np.ndarray:
    t = np.arange(int(1.2 * RATE)) / RATE
    return np.concatenate([np.zeros(int(0.3 * RATE)), 0.3 * np.sin(2 * np.pi * 300 * t),
                           np.zeros(int(1.5 * RATE))]).astype(np.float32)


def write_pcap(x: np.ndarray, dest: Path) -> int:
    payload = g711.encode(x.astype(np.float32), "pcma")
    out = [struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1)]
    n = 0
    for n, k in enumerate(range(0, len(payload), 160)):
        rtp = struct.pack("!BBHII", 0x80, 8 | (0x80 if n == 0 else 0), n & 0xFFFF, (n * 160) & 0xFFFFFFFF,
                          0x1234) + payload[k:k + 160]
        udp = struct.pack("!HHHH", 6000, 6000, 8 + len(rtp), 0) + rtp
        ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(udp), 0, 0, 64, 17, 0, LOCAL, LOCAL)
        ip = ip[:10] + struct.pack("!H", checksum(ip)) + ip[12:]
        frame = b"\0" * 12 + b"\x08\x00" + ip + udp
        us = n * 20000
        out.append(struct.pack("<IIII", us // 1000000, us % 1000000, len(frame), len(frame)) + frame)
    dest.write_bytes(b"".join(out))
    return n + 1


def parse_clip(spec: str) -> tuple[Path, float]:
    path, sep, at = spec.rpartition("@")
    if not sep:
        raise argparse.ArgumentTypeError(f"{spec!r}: DOSYA.wav@SANIYE biçiminde olmalı")
    return Path(path), float(at)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SIPp için arayan sesi (pcap)")
    ap.add_argument("--ses", action="append", default=[], type=parse_clip, metavar="DOSYA.wav@SANIYE")
    ap.add_argument("--sure", type=float, default=30.0, help="zaman çizgisinin uzunluğu (sn)")
    ap.add_argument("--cikti", type=Path, default=Path(__file__).with_name("konusma.pcap"))
    a = ap.parse_args(argv)
    x = timeline([(read_wav(p), at) for p, at in a.ses], a.sure) if a.ses else tone()
    n = write_pcap(x, a.cikti)
    print(f"{a.cikti} ({n} paket, {n * 0.02:.1f} sn)")
    for p, at in a.ses:
        print(f"  {at:6.1f} sn: {p.name} ({len(read_wav(p)) / RATE:.2f} sn)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
