"""Bilgisayar mikrofonundan geniş bantlı (16 kHz, tek kanal, 16 bit) kayıt: STT ölçümünde telefon hattıyla
karşılaştırmak için (tests/eval/okuma_hisirti.tsv). Enter'a basınca durur.

    pip install sounddevice
    python tools/stt/mikrofon_kaydet.py okuma_h_ham.wav
    python tools/stt/okuma_bol.py okuma_h_ham.wav --cikti okuma_h --tsv tests/eval/okuma_hisirti.tsv

Kayıt kişisel veridir: repoya girmez (.gitignore: *.wav), ölçümden sonra silin.
"""
from __future__ import annotations

import argparse
import queue
import sys
import threading
import wave
from pathlib import Path

SR = 16000


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cikti", type=Path)
    ap.add_argument("--aygit", default=None, help="giriş aygıtı (ad ya da numara); boş: varsayılan mikrofon")
    a = ap.parse_args(argv)
    try:
        import sounddevice as sd
    except ImportError:
        print("sounddevice gerekli: pip install sounddevice", file=sys.stderr)
        return 1
    chunks: queue.Queue[bytes] = queue.Queue()
    stop = threading.Event()

    def on_audio(data, frames, time_info, status) -> None:
        if status:
            print(f"uyarı: {status}", file=sys.stderr)
        chunks.put(bytes(data))

    device = int(a.aygit) if a.aygit and a.aygit.isdigit() else a.aygit
    with wave.open(str(a.cikti), "wb") as w, sd.RawInputStream(samplerate=SR, channels=1, dtype="int16",
                                                               device=device, callback=on_audio):
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        print(f"Kayıt başladı ({SR} Hz). Cümleleri sırayla, aralarında ~1,5 sn susarak okuyun; bitince Enter.")
        threading.Thread(target=lambda: (input(), stop.set()), daemon=True).start()
        frames = 0
        while not stop.is_set() or not chunks.empty():
            try:
                data = chunks.get(timeout=0.1)
            except queue.Empty:
                continue
            w.writeframes(data)
            frames += len(data) // 2
    print(f"{a.cikti}: {frames / SR:.1f} sn")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
