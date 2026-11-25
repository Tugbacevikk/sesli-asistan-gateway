"""Sesli deneme: okuma listesi ve "söylenen → duyulan → gidilen yer" raporu.

Akış (demo, kendi makinende):
  1. Listeyi yazdır / ekranda aç:
         python tools/deneme/sesli_rapor.py liste > okuma_listesi.md
  2. Servisleri aç, tarayıcıda /ui sayfasından her senaryo için AYRI çağrı yap (listedeki sırayla): ilk cümleyi
     söyle; asistan soru sorarsa listedeki sıradaki cevabı söyle; aktarınca ya da bitince çağrıyı kapat.
     Yanlış okursan o çağrıyı kapatıp aynı senaryoyu yeniden yapabilirsin: rapor senaryo başına SON çağrıyı alır
     (`--tekrar-son`), istersen hepsini gösterir.
  3. Raporu çıkar (çağrı kayıtları: CALL_LOG_DIR, varsayılan cagri_kayitlari/; metinler için LOG_TEXTS=true):
         python tools/deneme/sesli_rapor.py rapor --gun 2026-11-25 --baslangic 14:00 --cikti sesli_rapor.md

Eşleme: çağrılar başlama saatine göre sıralanır ve listedeki senaryolara sırayla verilir. Bir senaryo atlandıysa ya
da araya başka bir çağrı girdiyse `--atla S07` / `--baslangic SS:DD` ile düzelt. Her çağrı için ilk duyulan söz
listedeki ilk cümleyle karşılaştırılır; çok farklıysa (sözcük benzerliği < %30) uyarı yazılır: eşleme kaymış olabilir.

Sonuç türleri:
  DOĞRU        beklenen yere gitti (ya da beklendiği gibi aktarılmadı / operatöre gitti)
  YANLIŞ YER   başka bir dahiliye aktarıldı (en kötü hata)
  İZİNSİZ      aktarılmaması gereken hatta aktarıldı
  GİTMEDİ      aktarma beklenirken aktarılmadı (soru, "bulamadım", operatör)
STT payı: söylenen ile duyulan arasındaki sözcük hata oranı (WER). Yazıyla doğrulanmış bir senaryoda (liste CI'da
%100) YANLIŞ / GİTMEDİ varsa ve WER yüksekse hata STT'de; WER düşükse anlama / kural tarafında.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
LISTE = ROOT / "tests/data/diyalog_seti_sesli_deneme.yaml"


def _sade(x: str) -> list[str]:
    x = x.replace("I", "ı").replace("İ", "i").lower()
    return re.findall(r"[a-zçğıöşüâîû0-9]+", x)


def wer(ref: str, hyp: str) -> float:
    """Sözcük hata oranı (Levenshtein, sözcük düzeyinde)."""
    r, h = _sade(ref), _sade(hyp)
    if not r:
        return 0.0 if not h else 1.0
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev, d[j] = d[j], cur
    return d[len(h)] / len(r)


def benzerlik(a: str, b: str) -> float:
    x, y = set(_sade(a)), set(_sade(b))
    return len(x & y) / max(1, len(x | y))


def liste_oku(yol: Path = LISTE) -> list[dict[str, Any]]:
    import yaml

    return yaml.safe_load(yol.read_text(encoding="utf-8"))


@dataclass
class Cagri:
    dosya: Path
    basladi: str
    turlar: list[dict[str, Any]]
    sonuc: str
    kanal: str
    aktarilan: list[str] = field(default_factory=list)
    operator: bool = False

    @property
    def duyulan(self) -> list[str]:
        return [t["heard"] for t in self.turlar if t.get("heard") and not t["heard"].startswith("[")]


def cagri_oku(dosya: Path) -> Cagri | None:
    metin = dosya.read_text(encoding="utf-8", errors="replace")
    if "--- özet ---" not in metin:
        return None
    try:
        ozet = json.loads(metin.split("--- özet ---", 1)[1])
    except ValueError:
        return None
    turlar = ozet.get("turns") or []
    eylemler = [a for t in turlar for a in t.get("actions") or []]
    return Cagri(dosya=dosya, basladi=ozet.get("started") or "", turlar=turlar, sonuc=ozet.get("outcome") or "",
                 kanal=ozet.get("channel") or "",
                 aktarilan=[a.split(":", 1)[1] for a in eylemler if a.startswith("transfer:")],
                 operator=any(a.startswith("operator") for a in eylemler) or ozet.get("outcome") == "operator")


def degerlendir(sen: dict[str, Any], c: Cagri) -> str:
    b = sen["beklenen"]
    yasak = set(b.get("yasak_dahili") or [])
    if yasak & set(c.aktarilan):
        return "İZİNSİZ"
    if b["eylem"] == "transfer":
        if not c.aktarilan:
            return "GİTMEDİ"
        return "DOĞRU" if c.aktarilan[-1] == b["dahili"] else "YANLIŞ YER"
    if b["eylem"] == "operator":
        return "DOĞRU" if c.operator and not c.aktarilan else ("YANLIŞ YER" if c.aktarilan else "GİTMEDİ")
    return "YANLIŞ YER" if c.aktarilan else "DOĞRU"          # aktarma_yok


def nereye(c: Cagri) -> str:
    if c.aktarilan:
        return " → ".join(c.aktarilan)
    if c.operator:
        return "operatör"
    return c.sonuc or "-"


def komut_liste(a: argparse.Namespace) -> int:
    print("# Sesli deneme okuma listesi\n")
    print("Her satır **ayrı bir çağrı**. İlk cümleyi söyle; asistan sorarsa ok işaretinden sonrakini söyle.\n")
    for sen in liste_oku():
        konusma = sen["konusma"]
        ek = "".join(f"  →  (sorarsa) «{x}»" for x in konusma[1:])
        print(f"- **{sen['id']}** «{konusma[0]}»{ek}  \n  _{sen['aciklama']}_")
    return 0


def komut_rapor(a: argparse.Namespace) -> int:
    klasor = Path(a.klasor) / a.gun if a.gun else Path(a.klasor)
    dosyalar = sorted(klasor.rglob("*.log"))
    cagrilar = [c for f in dosyalar if (c := cagri_oku(f)) is not None and c.duyulan]
    if a.kanal:
        cagrilar = [c for c in cagrilar if c.kanal == a.kanal]
    if a.baslangic:
        cagrilar = [c for c in cagrilar if c.basladi[11:16] >= a.baslangic]
    cagrilar.sort(key=lambda c: c.basladi)
    senaryolar = [s for s in liste_oku() if s["id"] not in set(a.atla or [])]
    if a.ilk:
        senaryolar = senaryolar[[s["id"] for s in senaryolar].index(a.ilk):]
    if not cagrilar:
        print(f"{klasor} altında metinli çağrı kaydı yok (CALL_LOG_DIR ve LOG_TEXTS=true olmalı).", file=sys.stderr)
        return 1

    # senaryo başına çağrı: sırayla; ilk sözü listedekine benzemeyen ardışık çağrı aynı senaryonun yeniden denemesi
    # sayılır (--tekrar-son ile sonuncusu alınır)
    eslesme: list[tuple[dict[str, Any], list[Cagri]]] = []
    i = 0
    for sen in senaryolar:
        if i >= len(cagrilar):
            break
        grup = [cagrilar[i]]
        i += 1
        while (a.tekrar_son and i < len(cagrilar)
               and benzerlik(cagrilar[i].duyulan[0], sen["konusma"][0])
               > benzerlik(cagrilar[i].duyulan[0], _sonraki_ilk(senaryolar, sen))):
            grup.append(cagrilar[i])
            i += 1
        eslesme.append((sen, grup))

    satirlar, sayac = [], {}
    for sen, grup in eslesme:
        c = grup[-1]
        sonuc = degerlendir(sen, c)
        sayac[sonuc] = sayac.get(sonuc, 0) + 1
        w = wer(sen["konusma"][0], c.duyulan[0])
        kayma = benzerlik(sen["konusma"][0], c.duyulan[0]) < 0.3
        beklenen = sen["beklenen"].get("dahili") or sen["beklenen"]["eylem"]
        satirlar.append({"id": sen["id"], "soylenen": " / ".join(sen["konusma"]), "duyulan": " / ".join(c.duyulan),
                         "wer": round(w, 2), "beklenen": beklenen, "gidilen": nereye(c), "sonuc": sonuc,
                         "cevaplar": " / ".join(t.get("reply", "") for t in c.turlar if t.get("reply")),
                         "deneme": len(grup), "uyari": "eşleme kaymış olabilir" if kayma else "",
                         "dosya": str(c.dosya)})
    n = len(satirlar)
    md = [f"# Sesli deneme raporu ({klasor}, {datetime.now():%d.%m.%Y %H:%M})\n",
          f"{n} senaryo · " + " · ".join(f"{k} {v}" for k, v in sorted(sayac.items()))
          + f" · doğruluk %{100 * sayac.get('DOĞRU', 0) / max(1, n):.1f}"
          + f" · ortalama WER {sum(r['wer'] for r in satirlar) / max(1, n):.2f}\n",
          "| # | Söylenen | Duyulan (STT) | WER | Beklenen | Gidilen | Sonuç |", "|---|---|---|---|---|---|---|"]
    for r in satirlar:
        isaret = "" if r["sonuc"] == "DOĞRU" else "**"
        not_ = f" ⚠ {r['uyari']}" if r["uyari"] else ""
        md.append(f"| {r['id']} | {r['soylenen']} | {r['duyulan']}{not_} | {r['wer']} | {r['beklenen']} | "
                  f"{r['gidilen']} | {isaret}{r['sonuc']}{isaret} |")
    hatalar = [r for r in satirlar if r["sonuc"] != "DOĞRU"]
    if hatalar:
        md.append("\n## Hatalı senaryolar\n")
        for r in hatalar:
            yer = "büyük olasılıkla STT" if r["wer"] >= 0.3 else "anlama / kural"
            md.append(f"- **{r['id']}** ({r['sonuc']}, {yer}): söylenen «{r['soylenen']}», duyulan «{r['duyulan']}», "
                      f"asistan «{r['cevaplar']}»  \n  `{r['dosya']}`")
    kalan = len(cagrilar) - sum(len(g) for _, g in eslesme)
    if kalan > 0:
        md.append(f"\n{kalan} çağrı hiçbir senaryoyla eşlenmedi (liste bitti).")
    cikti = "\n".join(md) + "\n"
    print(cikti)
    if a.cikti:
        Path(a.cikti).write_text(cikti, encoding="utf-8")
        Path(a.cikti).with_suffix(".json").write_text(json.dumps(satirlar, ensure_ascii=False, indent=1),
                                                       encoding="utf-8")
        print(f"yazıldı: {a.cikti} (+ .json)", file=sys.stderr)
    return 0


def _sonraki_ilk(senaryolar: list[dict[str, Any]], sen: dict[str, Any]) -> str:
    ids = [s["id"] for s in senaryolar]
    k = ids.index(sen["id"]) + 1
    return senaryolar[k]["konusma"][0] if k < len(senaryolar) else ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    alt = ap.add_subparsers(dest="komut", required=True)
    alt.add_parser("liste", help="okuma listesini Markdown olarak yazar")
    r = alt.add_parser("rapor", help="çağrı kayıtlarından rapor")
    r.add_argument("--klasor", default="cagri_kayitlari", help="CALL_LOG_DIR")
    r.add_argument("--gun", default=datetime.now().strftime("%Y-%m-%d"), help="gün klasörü (boş: hepsi)")
    r.add_argument("--baslangic", default="", help="bu saatten (SS:DD) önce başlayan çağrılar alınmaz")
    r.add_argument("--kanal", default="web", help="web (arayüz) | santral | boş: hepsi")
    r.add_argument("--ilk", default="", help="ilk çağrının senaryosu (ör. S11: listenin ortasından başlandıysa)")
    r.add_argument("--atla", action="append", help="yapılmayan senaryo (birden çok kez verilebilir)")
    r.add_argument("--tekrar-son", action=argparse.BooleanOptionalAction, default=True,
                   help="aynı senaryonun art arda denemelerinden sonuncusu (varsayılan açık)")
    r.add_argument("--cikti", default="", help="Markdown dosyası (yanına .json da yazılır)")
    a = ap.parse_args(argv)
    return komut_liste(a) if a.komut == "liste" else komut_rapor(a)


if __name__ == "__main__":
    raise SystemExit(main())
