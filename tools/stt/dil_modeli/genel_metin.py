"""Santral dil modeli derlemine katılacak **genel Türkçe** cümleler (ADR-028, ölçüm 27.11.2026).

Yalnız santral cümleleriyle eğitilen dil modeli uzun / kalıp dışı cümleyi erken bitiriyor ("…destek sağlayan bir
yardımdır" → "meydimdir"): gerçek insan sesli FLEURS'ta ağırlık 0,2'de +8, 0,3'te +18 puan bozulma. Derleme genel
cümle katılınca (derlem.py --genel) model uzun cümleyi de tanır; aynı iki setle yeniden ölçülür.

    python tools/stt/dil_modeli/genel_metin.py --cikti genel.txt
    python tools/stt/dil_modeli/genel_metin.py --dosya benim_cc0_metnim.txt --cikti genel.txt

Kaynak (varsayılan): FLEURS Türkçe **train + dev** metinleri (Google, CC BY 4.0; ~3.400 cümle, yalnız metin, ~1,7 MB).
Ölçüm FLEURS **test** cümleleriyle yapılır: cümleler ayrı, ama üslup aynı (Vikipedi): genel konuşmadaki iyileşme
biraz iyimser ölçülebilir; karar santral setindeki kazançla birlikte verilir. `--dosya` ile başka (CC0 / izinli)
metin de verilebilir, satır başına bir cümle.

Biçim STT'nin yazdığı gibi: küçük harf, noktalamasız, sayılar sözle ("1999" → "bin dokuz yüz doksan dokuz"), özel
adda kesme işareti korunur.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
from sesli_asistan.gateway.core.search import number_to_words  # noqa: E402
from sesli_asistan.libs.text.normalizer import turkish_lower  # noqa: E402

FLEURS = "https://huggingface.co/datasets/google/fleurs/resolve/main/data/tr_tr/{}.tsv"
_HARF = "a-zçğıöşüâîû"


def normalize(text: str) -> str:
    """STT biçimi: küçük harf, sayılar sözle, kesme işareti dışında noktalama yok; olmayacak satır → ""."""
    t = turkish_lower(text)
    t = re.sub(r"(?<=\d)\.(?=\d{3})", "", t)            # binlik ayırıcı: 800.000
    t = re.sub(r"(\d)'", r"\1", t)                         # sayıdan sonra ek bitişik: 800000'den → …binden
    t = re.sub(r"\d+", lambda m: f" {number_to_words(int(m.group()))}" if len(m.group()) <= 9 else " ", t)
    t = re.sub(rf"[^{_HARF}' ]", " ", t)
    t = re.sub(r"\s*'\s*", "'", t)
    t = re.sub(r"(^|\s)'|'(\s|$)", " ", t)
    words = t.split()
    if len(words) < 3 or any(not re.fullmatch(rf"[{_HARF}]+('[{_HARF}]+)?", w) for w in words):
        return ""
    return " ".join(words)


def fleurs(split: str, onbellek: Path) -> list[str]:
    path = onbellek / f"{split}.tsv"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"indiriliyor: FLEURS tr {split}.tsv", file=sys.stderr)
        urllib.request.urlretrieve(FLEURS.format(split), path)  # noqa: S310 (sabit https adresi)
    rows = csv.reader(path.read_text(encoding="utf-8").splitlines(), delimiter="\t", quoting=csv.QUOTE_NONE)
    return [r[2] for r in rows if len(r) >= 3]          # ham metin (sayılar rakamla; normalize sözle yazar)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dosya", type=Path, action="append", default=[], help="ek metin (satır başına cümle)")
    ap.add_argument("--fleurs-yok", action="store_true", help="FLEURS indirilmesin (yalnız --dosya)")
    ap.add_argument("--onbellek", type=Path, default=Path("veri_onbellek/fleurs_tr"))
    ap.add_argument("--cikti", type=Path, required=True)
    a = ap.parse_args(argv)
    ham: list[str] = []
    if not a.fleurs_yok:
        ham += fleurs("train", a.onbellek) + fleurs("dev", a.onbellek)
    for p in a.dosya:
        ham += p.read_text(encoding="utf-8").splitlines()
    out = list(dict.fromkeys(x for x in (normalize(s) for s in ham) if x))
    a.cikti.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"{a.cikti}: {len(out)} genel cümle ({len(ham)} ham satırdan), "
          f"ortalama {sum(len(x.split()) for x in out) / max(1, len(out)):.1f} sözcük")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
