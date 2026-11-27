"""Hazır veri setinden STT ölçümü: FLEURS Türkçe test (Google, CC BY 4.0; ~750 cümle, gerçek insan sesleri, farklı
konuşanlar) indirilir, santral hattından geçmiş gibi kodlanır (G.711 8 kHz / G.722), istenirse uğultu katılır.
Çıkan kayıtlar karsilastir.py ile ölçülür.

    python tools/stt/hazir_set.py --adet 200 --hat g711 --hat g722 --arka ugultu:15 --cikti fleurs_set
    python tools/stt/karsilastir.py --tsv fleurs_set/dogrular.tsv --sherpa models/turkish-stt-66m-v2 \\
        --lm models/santral_lm2.onnx --lm-olcek 0.2 --json sonuc_fleurs.json

Ne ölçer, ne ölçmez: genel Türkçe konuşmanın (haber / ansiklopedi cümleleri) telefon hattında ne kadar doğru
anlaşıldığı ve santral dil modelinin genel konuşmayı **bozup bozmadığı** (dil modeli santral cümlelerine göre
eğitildi; genel cümlede WER'i artırıyorsa ağırlık fazladır). Santral sözlerini (rehberdeki adlar, "dereköy") ölçmez:
onun için okuma listesi (sesli_rapor.py) ve yapay set (sesli_set.py). FLEURS metninde sayılar rakamla yazılır, STT
sözle yazar: sayılı cümlelerde WER biraz yüksek çıkar (iki durumu karşılaştırırken etkisi aynıdır).

İndirilen: yalnız test kümesi (~465 MB, bir kez; --onbellek). Kayıtlar repoya girmez.
"""
from __future__ import annotations

import argparse
import csv
import io
import random
import sys
import tarfile
import urllib.request
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sesli_set import SR, Background, hum, line, mix, write_wav  # noqa: E402

BASE = "https://huggingface.co/datasets/google/fleurs/resolve/main/data/tr_tr"


def indir(url: str, hedef: Path) -> Path:
    if not hedef.exists():
        print(f"indiriliyor: {url}", file=sys.stderr)
        hedef.parent.mkdir(parents=True, exist_ok=True)
        tmp = hedef.with_suffix(hedef.suffix + ".part")
        urllib.request.urlretrieve(url, tmp)  # noqa: S310 (sabit https adresi)
        tmp.rename(hedef)
    return hedef


def metinler(tsv: Path) -> dict[str, str]:
    """FLEURS tsv: id, dosya, ham metin, normal metin, … → dosya → normal (küçük harf, noktalamasız) metin."""
    out = {}
    for rec in csv.reader(tsv.read_text(encoding="utf-8").splitlines(), delimiter="\t", quoting=csv.QUOTE_NONE):
        if len(rec) >= 4:
            out[rec[1]] = rec[3].strip()
    return out


def oku_wav(veri: bytes) -> np.ndarray:
    with wave.open(io.BytesIO(veri)) as w:
        if w.getframerate() != SR or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError("FLEURS wav 16 kHz mono 16 bit bekleniyordu")
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--adet", type=int, default=200, help="kaç cümle (rastgele, tohumla aynı; 0: hepsi)")
    ap.add_argument("--hat", choices=("g711", "g722", "genis"), action="append", help="varsayılan g711 ve g722")
    ap.add_argument("--arka", type=Background.parse, action="append", default=[],
                    help="ugultu:SNR (temiz her zaman var); konuşma arka planı burada yok")
    ap.add_argument("--onbellek", type=Path, default=Path("veri_onbellek/fleurs_tr"))
    ap.add_argument("--cikti", type=Path, default=Path("fleurs_set"))
    ap.add_argument("--tohum", type=int, default=7)
    a = ap.parse_args(argv)
    if any(b.kind != "ugultu" for b in a.arka):
        ap.error("yalnız --arka ugultu:SNR")

    tsv = indir(f"{BASE}/test.tsv", a.onbellek / "test.tsv")
    tar = indir(f"{BASE}/audio/test.tar.gz", a.onbellek / "test.tar.gz")
    dogru = metinler(tsv)
    rng = np.random.default_rng(a.tohum)
    lines = a.hat or ["g711", "g722"]
    backgrounds = [Background("temiz"), *a.arka]

    with tarfile.open(tar) as t:
        uyeler = [m for m in t.getmembers() if m.isfile() and Path(m.name).name in dogru]
        uyeler.sort(key=lambda m: m.name)
        if a.adet:
            uyeler = random.Random(a.tohum).sample(uyeler, min(a.adet, len(uyeler)))
        a.cikti.mkdir(parents=True, exist_ok=True)
        satirlar = []
        for i, m in enumerate(uyeler, 1):
            f = t.extractfile(m)
            if f is None:
                continue
            x = oku_wav(f.read())
            metin = dogru[Path(m.name).name]
            for kind in lines:
                for b in backgrounds:
                    y = x if b.kind == "temiz" else mix(x, hum(len(x), rng), b.snr_db)
                    out, rate = line(y, kind)
                    ad = f"f{i:03d}_{kind}_{b.tag}.wav"
                    write_wav(a.cikti / ad, out, rate)
                    satirlar.append(f"{ad}\t{metin}")
    (a.cikti / "dogrular.tsv").write_text("\n".join(satirlar) + "\n", encoding="utf-8")
    print(f"{len(satirlar)} kayıt ({len(uyeler)} cümle): {a.cikti}/ (doğrular: {a.cikti / 'dogrular.tsv'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
