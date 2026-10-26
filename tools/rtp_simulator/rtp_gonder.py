"""Santral yerine: bir ses dosyasını RTP olarak gerçek zaman hızında gönderir, gelen cevabı kaydeder.

Kaynak: Vietnamese-speech-to-text-models `scripts/rtp_gonder.py`.
Uyarlanan: PCMA (A-law, varsayılan) eklendi; kodlama `libs/audio/g711` ile ortak; soxr yerine scipy;
cevap sesini aynı soketten dinleyip WAV'a yazma (`--cevap`); L16 ve SDP çıkışı alınmadı.

    python tools/rtp_simulator/rtp_gonder.py kayit.wav --hedef 127.0.0.1:10012
    python tools/rtp_simulator/rtp_gonder.py kayit.wav --codec pcmu --kayip 0.02 --karistir 0.05
    python tools/rtp_simulator/rtp_gonder.py kayit.wav --cevap cevap.wav --sonra 5

ffmpeg ile de gönderilebilir:
    ffmpeg -re -i kayit.wav -ar 8000 -ac 1 -c:a pcm_alaw -f rtp rtp://127.0.0.1:10012
"""
from __future__ import annotations

import argparse
import random
import socket
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from sesli_asistan.libs.audio import g711  # noqa: E402
from sesli_asistan.libs.audio.resample import resample  # noqa: E402

RATE = 8000
PTIME = 0.02
N = int(RATE * PTIME)  # 160


def packets(audio8k: np.ndarray, codec: str, ssrc: int = 0x1234ABCD):
    """8 kHz float32 → RTP paketleri (20 ms)."""
    pt = g711.PAYLOAD_TYPE[codec]
    for i, k in enumerate(range(0, len(audio8k), N)):
        chunk = audio8k[k:k + N]
        if len(chunk) < N:
            chunk = np.pad(chunk, (0, N - len(chunk)))
        payload = g711.encode(chunk, codec)
        b1 = pt | (0x80 if i == 0 else 0)
        yield bytes([0x80, b1]) + (i & 0xFFFF).to_bytes(2, "big") + (k & 0xFFFFFFFF).to_bytes(4, "big") \
            + ssrc.to_bytes(4, "big") + payload


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dosya")
    ap.add_argument("--hedef", default="127.0.0.1:10000")
    ap.add_argument("--codec", choices=list(g711.CODECS), default="pcma")
    ap.add_argument("--hiz", type=float, default=1.0, help="1 = gerçek zaman, 0 = beklemeden")
    ap.add_argument("--kayip", type=float, default=0.0, help="paket kaybı oranı (0-1)")
    ap.add_argument("--karistir", type=float, default=0.0, help="sırası bozulan paket oranı")
    ap.add_argument("--tohum", type=int, default=0)
    ap.add_argument("--cevap", default=None, help="gelen RTP cevabını bu WAV dosyasına yaz")
    ap.add_argument("--sonra", type=float, default=3.0, help="gönderim bittikten sonra cevap bekleme (sn)")
    a = ap.parse_args(argv)

    import soundfile as sf

    audio, sr = sf.read(a.dosya, dtype="float32", always_2d=True)
    audio = resample(audio.mean(axis=1), sr, RATE)
    host, port = a.hedef.rsplit(":", 1)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", 0))
    sock.setblocking(False)
    rng = random.Random(a.tohum)
    reply: list[np.ndarray] = []

    def drain():
        while True:
            try:
                data = sock.recv(4096)
            except BlockingIOError:
                return
            if len(data) > 12 and data[1] & 0x7F in g711.CODEC_OF_PAYLOAD:
                reply.append(g711.decode(data[12:], g711.CODEC_OF_PAYLOAD[data[1] & 0x7F]))

    t0, held, sent = time.monotonic(), None, 0
    for i, pkt in enumerate(packets(audio, a.codec)):
        if a.hiz > 0:
            delay = t0 + i * PTIME / a.hiz - time.monotonic()
            if delay > 0:
                time.sleep(delay)
        drain()
        if rng.random() < a.kayip:
            continue
        if held is None and rng.random() < a.karistir:
            held = pkt  # bir sonrakinden sonra gönder: sıra bozulur
            continue
        sock.sendto(pkt, (host, int(port)))
        sent += 1
        if held is not None:
            sock.sendto(held, (host, int(port)))
            held, sent = None, sent + 1
    end = time.monotonic() + a.sonra
    while time.monotonic() < end:
        drain()
        time.sleep(0.01)
    print(f"{sent} paket gönderildi ({len(audio) / RATE:.1f} sn ses), {len(reply)} cevap paketi alındı")
    if a.cevap and reply:
        sf.write(a.cevap, np.concatenate(reply), RATE)
        print(f"cevap: {a.cevap}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
