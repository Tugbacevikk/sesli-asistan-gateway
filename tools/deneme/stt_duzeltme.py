"""Sesli denemeden kelime düzeltmesi önerisi (ADR-033): STT'nin rehberdeki adları / birimleri / unvanları nasıl
bozduğunu bulur ("muhasebe" → "masebe", "asayiş başkanlığı" → "masaj bakanlığı").

    python tools/deneme/sesli_rapor.py rapor --cikti sesli_rapor.md          # yanına sesli_rapor.json
    python tools/deneme/stt_duzeltme.py sesli_rapor.json --rehber data/demo_rehber.json --cikti duzeltmeler.json

Her senaryoda söylenen (okuma listesi) ve duyulan (STT) cümleler sözcük sözcük hizalanır. Ayrışan parçalardan,
söylenen tarafında rehber sözcüğü geçenler öneri olur: "duyulan → doğrusu", kaç kez görüldüğüyle. Öneri, ayar ekranının
kurallarına uyar (1–5 sözcük; evet / hayır / operatör ve rehberdeki gerçek bir ad ya da birim düzeltilemez).

Uygulama: Ayarlar → Kelime düzeltmeleri'ne satır satır girin (önce "Dene" ile bakın) ya da `--cikti` dosyasındaki
`stt_duzeltmeleri`ni kurum dosyasına (ABBREVIATIONS_PATH) ekleyin. **Bir kez görülen öneriye dikkat:** okuma hatası
da olabilir; en az iki kez görülenler (`--en-az 2`) daha güvenlidir.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from sesli_asistan.gateway.core.phrases import NEGATION_WORDS, OPERATOR_WORDS, YES_WORDS  # noqa: E402
from sesli_asistan.gateway.core.search import strip_suffix  # noqa: E402
from sesli_asistan.libs.text.normalizer import fold_tr_words  # noqa: E402

AYRILMAZ = {*YES_WORDS, *NEGATION_WORDS, *OPERATOR_WORDS}
EN_COK_SOZCUK = 5          # ayar ekranıyla aynı (settings_service.MAX_CORRECTION_WORDS)


def sozcukler(metin: str) -> list[str]:
    """Özgün (Türkçe harfli, küçük) sözcükler."""
    metin = metin.replace("I", "ı").replace("İ", "i").lower()
    return re.findall(r"[a-zçğıöşüâîû0-9]+", metin)


def katla(w: str) -> str:
    return " ".join(fold_tr_words(w))


def hizala(ref: list[str], hyp: list[str]) -> list[tuple[list[str], list[str]]]:
    """Sözcük hizalaması (Levenshtein); ayrışan ardışık parçalar: [(söylenen parçası, duyulan parçası)]."""
    a, b = [katla(w) for w in ref], [katla(w) for w in hyp]
    n, m = len(a), len(b)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] != b[j - 1]))
    ops: list[tuple[int | None, int | None]] = []          # (söylenen sırası, duyulan sırası); eşitse ikisi de
    i, j = n, m
    while i or j:
        if i and j and a[i - 1] == b[j - 1] and d[i][j] == d[i - 1][j - 1]:
            ops.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif i and j and d[i][j] == d[i - 1][j - 1] + 1:
            ops.append((i - 1, -(j - 1) - 1))               # değişim: duyulan eksi işaretle
            i, j = i - 1, j - 1
        elif i and d[i][j] == d[i - 1][j] + 1:
            ops.append((i - 1, None))
            i -= 1
        else:
            ops.append((None, -(j - 1) - 1))
            j -= 1
    ops.reverse()
    out: list[tuple[list[str], list[str]]] = []
    r: list[str] = []
    h: list[str] = []
    for x, y in ops:
        if x is not None and y is not None and y >= 0:      # eşit sözcük: parça biter
            if r or h:
                out.append((r, h))
            r, h = [], []
            continue
        if x is not None:
            r.append(ref[x])
        if y is not None:
            h.append(hyp[-y - 1])
    if r or h:
        out.append((r, h))
    return out


def rehber_sozleri(yol: Path) -> tuple[set[str], set[str]]:
    """(rehber sözcükleri, rehberdeki gerçek ad / birim adları), katlanmış."""
    data = json.loads(yol.read_text(encoding="utf-8"))
    kelime: set[str] = set()
    gercek: set[str] = set()
    for c in data["contacts"]:
        ad = " ".join(x for x in (c.get("first_name"), c.get("last_name")) if x)
        adlar = [ad, c.get("department", ""), c.get("title", ""), *c.get("aliases", []),
                 *c.get("department_aliases", [])]
        for n in adlar:
            if not n:
                continue
            k = katla(n)
            if k:
                kelime.update(k.split())
            if n in (ad, c.get("department", "")):
                gercek.add(k)
    return {w for w in kelime if len(w) >= 3}, gercek


def rehberde(w: str, kelime: set[str]) -> bool:
    """Söylenen sözcük (ekli olabilir) bir rehber sözcüğü mü: "muhasebeden" → muhasebe."""
    k = katla(w)
    return k in kelime or strip_suffix(k) in kelime or any(len(v) >= 4 and k.startswith(v) for v in kelime)


def ortak_ek(duyulan: list[str], dogru: list[str]) -> tuple[list[str], list[str]]:
    """Doğru son sözcüğün eki, duyulanda da aynıysa ikisinden atılır ("masaj bakanlığına" / "asayiş başkanlığına" →
    "masaj bakanlığı" / "asayiş başkanlığı"): düzeltme eki korur, kural ekli ve eksiz biçime birlikte uyar."""
    x, y = katla(duyulan[-1]), katla(dogru[-1])
    k = len(y) - len(strip_suffix(y)) if y.startswith(strip_suffix(y)) else 0
    if k > 1 and dogru[-1][:-k].endswith("ğ"):
        k -= 1                                     # "başkanlığ" okunaksız: "başkanlığı" + "na" (eki yine korunur)
    if k and x.endswith(y[-k:]) and len(x) - k >= 2:
        return [*duyulan[:-1], duyulan[-1][:-k]], [*dogru[:-1], dogru[-1][:-k]]
    return duyulan, dogru


def oneriler(satirlar: list[dict[str, Any]], kelime: set[str], gercek: set[str]) -> Counter[tuple[str, str]]:
    sayac: Counter[tuple[str, str]] = Counter()
    for s in satirlar:
        soylenen = [x.strip() for x in str(s.get("soylenen", "")).split(" / ")]
        duyulan = [x.strip() for x in str(s.get("duyulan", "")).split(" / ")]
        for ref_metin, hyp_metin in zip(soylenen, duyulan, strict=False):
            for r, h in hizala(sozcukler(ref_metin), sozcukler(hyp_metin)):
                if not r or not h or len(h) > EN_COK_SOZCUK or len(r) > EN_COK_SOZCUK + 1:
                    continue                                # yutulan / eklenen sözcük: düzeltme işi değil
                if not any(rehberde(w, kelime) for w in r):
                    continue                                # rehberle ilgisiz söz ("istiyorum" → "isterim")
                if len(h) == 1 and len(r) == 1 and strip_suffix(katla(h[0])) == strip_suffix(katla(r[0])):
                    continue                                # yalnız ek farkı ("muhasebeye" ↔ "muhasebeden")
                h2, r2 = ortak_ek(h, r)
                anahtar = " ".join(h2)
                if (len(h2) == 1 and katla(h2[0]) in AYRILMAZ) or katla(anahtar) in gercek:
                    continue                                # ayar ekranı kabul etmez
                if not 2 <= len(anahtar) <= 60 or katla(anahtar) == katla(" ".join(r2)):
                    continue
                sayac[(anahtar, " ".join(r2))] += 1
    return sayac


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rapor", nargs="+", help="sesli_rapor.json (birden çok deneme verilebilir)")
    ap.add_argument("--rehber", required=True, help="denemede kullanılan rehber (JSON)")
    ap.add_argument("--en-az", type=int, default=1, help="en az kaç kez görülen öneri (varsayılan 1)")
    ap.add_argument("--cikti", default="", help='{"stt_duzeltmeleri": {...}} JSON (kurum dosyasına eklenir)')
    a = ap.parse_args(argv)
    satirlar = [r for y in a.rapor for r in json.loads(Path(y).read_text(encoding="utf-8"))]
    kelime, gercek = rehber_sozleri(Path(a.rehber))
    sayac = oneriler(satirlar, kelime, gercek)
    secilen = [(k, v, n) for (k, v), n in sayac.most_common() if n >= a.en_az]
    # aynı duyulan söz iki farklı doğruya gidiyorsa öneri belirsiz: en sık olanı alınır, öbürü yazılır
    tablo: dict[str, str] = {}
    catisan: list[str] = []
    for k, v, _ in secilen:
        if k in tablo and tablo[k] != v:
            catisan.append(f"{k} → {v} (öbürü: {tablo[k]})")
        tablo.setdefault(k, v)
    print(f"{len(satirlar)} senaryo → {len(tablo)} öneri (en az {a.en_az} kez)\n")
    print("| Duyulan (STT) | Doğrusu | Kaç kez |\n|---|---|---|")
    for k, v, n in secilen:
        if tablo.get(k) == v:
            print(f"| {k} | {v} | {n} |")
    if catisan:
        print("\nBelirsiz (aynı söz iki farklı yere): " + "; ".join(catisan))
    if a.cikti:
        Path(a.cikti).write_text(json.dumps({"stt_duzeltmeleri": tablo}, ensure_ascii=False, indent=1) + "\n",
                                 encoding="utf-8")
        print(f"\nyazıldı: {a.cikti}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
