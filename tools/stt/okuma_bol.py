"""Okuma kaydını cümlelere ayırır: santralin 600 dahilisinde alınan uzun kayıt → k01.wav, k02.wav … (okuma listesi
sırasıyla), tools/stt/karsilastir.py --tsv tests/eval/okuma_listesi.tsv ile ölçülmeye hazır.

    python tools/stt/okuma_bol.py okuma-1793690000.wav --cikti okuma --tsv tests/eval/okuma_listesi.tsv

Cümleler STT servisiyle aynı cümle bölücüyle (Silero VAD) ayrılır; cümle sonu sessizliği burada uzun tutulur
(varsayılan 1000 ms) ki cümle içindeki duraklar bölmesin — okurken cümleler arasında ~1,5 sn susun. Parça sayısı
listeyle tutmazsa (öksürük, tekrar okunan cümle) dosyalar yine yazılır ve uyarılır: --atla ile o parçalar atılır.
Her parçanın başına / sonuna en çok 0,3 sn kayıttaki ses eklenir (kesilen hece kalmasın); pay komşu cümleye
taşmaz (aradaki sessizliğin yarısıyla sınırlı — 03.11.2026 ölçümünde taşan pay sonraki cümlenin ilk hecesini
ekliyordu: "teknik destek lütfen insan"). Kayıtlar kişisel veridir:
repoya girmez (.gitignore: okuma/), ölçümden sonra silin.
"""
from __future__ import annotations

import argparse
import csv
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from sesli_asistan.libs.audio.resample import resample  # noqa: E402
from sesli_asistan.stt.vad import SAMPLE_RATE, Segmenter, SegmenterConfig, Utterance, make_detector  # noqa: E402

PAD_S = 0.3


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path)) as w:
        if w.getsampwidth() != 2:
            raise SystemExit(f"{path}: yalnız 16 bit PCM")
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768
        if w.getnchannels() > 1:
            x = x.reshape(-1, w.getnchannels()).mean(axis=1)
        return x, w.getframerate()


def write_wav(path: Path, x: np.ndarray, rate: int) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


def split(x16: np.ndarray, detector, cfg: SegmenterConfig) -> list[Utterance]:
    seg = Segmenter(detector, cfg)
    out: list[Utterance] = []
    step = SAMPLE_RATE // 50
    for i in range(0, len(x16), step):
        out += [e for e in seg.feed(x16[i:i + step]) if isinstance(e, Utterance)]
    return out + seg.flush()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Okuma kaydını cümlelere ayırır")
    ap.add_argument("wav", type=Path)
    ap.add_argument("--cikti", type=Path, default=Path("okuma"))
    ap.add_argument("--tsv", type=Path, help="okuma listesi (dosya adları ve sayı buradan)")
    ap.add_argument("--vad-model", default="models/silero_vad.onnx")
    ap.add_argument("--esik", type=float, default=0.35)
    ap.add_argument("--sessizlik-ms", type=int, default=1000)
    ap.add_argument("--atla", type=int, action="append", default=[], help="bu sıradaki parçayı at (1'den başlar)")
    a = ap.parse_args(argv)

    x, rate = read_wav(a.wav)
    x16 = resample(x, rate, SAMPLE_RATE) if rate != SAMPLE_RATE else x
    # ön pay 350 ms'de sabit: kayıtların kendi payı (0,3 sn) var; STT'nin 500 ms'si okuma setlerini uzatıp önceki
    # ölçümlerle karşılaştırmayı bozardı
    cfg = SegmenterConfig(end_silence_ms=a.sessizlik_ms, pre_roll_ms=350)
    utts = split(x16, make_detector("silero", a.vad_model, a.esik), cfg)
    utts = [u for i, u in enumerate(utts, 1) if i not in a.atla]
    names = [f"k{i:02d}.wav" for i in range(1, len(utts) + 1)]
    if a.tsv:
        with a.tsv.open(encoding="utf-8") as f:
            listed = [r[0].strip() for r in csv.reader(f, delimiter="\t") if r and r[0].strip()
                      and not r[0].startswith("#")]
        if len(listed) != len(utts):
            print(f"UYARI: listede {len(listed)} cümle var, kayıtta {len(utts)} parça bulundu. Aşağıdaki sürelere "
                  "bakıp fazlalığı --atla ile atın ya da eksik cümleyi yeniden okuyun.")
        names = [*listed[:len(utts)], *names[len(listed):]]
    a.cikti.mkdir(parents=True, exist_ok=True)
    for i, (name, u) in enumerate(zip(names, utts, strict=True)):
        before = PAD_S if i == 0 else min(PAD_S, max(0.0, (u.start - utts[i - 1].end) / 2))
        after = PAD_S if i == len(utts) - 1 else min(PAD_S, max(0.0, (utts[i + 1].start - u.end) / 2))
        b, e = int((u.start - before) * rate), int((u.end + after) * rate)
        write_wav(a.cikti / name, x[max(0, b):e], rate)
        print(f"  {name}: {u.start:6.1f}–{u.end:6.1f} sn ({u.end - u.start:.1f} sn)")
    print(f"{len(utts)} parça → {a.cikti}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
