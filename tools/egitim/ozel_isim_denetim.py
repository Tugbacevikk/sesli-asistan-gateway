"""Eğitim verisinde özel isim denetimi: gerçek kişi, kurum, şirket ya da yer adı kalmasın.

    python tools/egitim/ozel_isim_denetim.py                 # tools/egitim/veri/*.jsonl
    python tools/egitim/ozel_isim_denetim.py --katı          # bulgu varsa çıkış kodu 1 (CI / eğitim öncesi)

Ne arar (söz, etiket ve sistem istemi ayrı ayrı):
1. Gerçek yer adları: Türkiye'nin 81 ili ve büyük ilçeleri (sözcük olarak; "Bursa", "Çankaya").
2. Şirket / marka adları: `YASAKLI` listesi (şirket adı dahil, depoya girmemeli) ve `--yasakli dosya.txt`.
3. Kişisel veri kalıpları: telefon, e-posta, T.C. kimlik no'ya benzeyen 11 hane, IBAN.
4. Envanter: verideki bütün ad, soyad ve kurum/yer sözcükleri, kaynağıyla. Her biri sentetik üreticilerin
   havuzunda olmalı (tools/rehber/*.py, data/*.json uydurma rehberler); havuz dışı bir ad bulgudur.
5. Ad + soyad çiftleri: aynı çift çok sık geçiyorsa model belli bir "kişiyi" ezberleyebilir (bilgi amaçlı).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from anlama_veri import REHBERLER  # noqa: E402

from sesli_asistan.gateway.core.models import Contact  # noqa: E402
from sesli_asistan.libs.text.normalizer import turkish_lower  # noqa: E402

ILLER = """adana adıyaman afyonkarahisar ağrı amasya ankara antalya artvin aydın balıkesir bilecik bingöl bitlis bolu
burdur bursa çanakkale çankırı çorum denizli diyarbakır edirne elazığ erzincan erzurum eskişehir gaziantep giresun
gümüşhane hakkari hatay ısparta mersin istanbul izmir kars kastamonu kayseri kırklareli kırşehir kocaeli konya
kütahya malatya manisa kahramanmaraş mardin muğla muş nevşehir niğde ordu rize sakarya samsun siirt sinop sivas
tekirdağ tokat trabzon tunceli şanlıurfa uşak van yozgat zonguldak aksaray bayburt karaman kırıkkale batman şırnak
bartın ardahan iğdır yalova karabük kilis osmaniye düzce""".split()
# büyük ilçeler (sık geçen; tam liste değil)
ILCELER = """çankaya keçiören yenimahalle mamak etimesgut sincan kadıköy üsküdar beşiktaş şişli bakırköy fatih
kartal pendik ümraniye esenyurt bağcılar karşıyaka bornova buca konak nilüfer osmangazi yıldırım inegöl gemlik
mudanya seyhan çukurova selçuklu meram karatay muratpaşa kepez tepebaşı odunpazarı atakum ilkadım""".split()
# şirket / marka adları: verinin hiçbir yerinde geçmemeli
YASAKLI = ["karel", "netgsm", "turkcell", "vodafone", "türk telekom", "aselsan", "havelsan", "sentra"]

KALIPLAR = {
    "telefon": re.compile(r"(?<!\d)(?:\+?90\s?)?0?5\d{2}\s?\d{3}\s?\d{2}\s?\d{2}(?!\d)"),
    "e-posta": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
    "tc_kimlik": re.compile(r"(?<!\d)[1-9]\d{10}(?!\d)"),
    "iban": re.compile(r"\bTR\d{2}\s?(?:\d{4}\s?){5}\d{2}\b", re.I),
}
# "sentra": tasarım belgesindeki API adı; sözde geçerse bulgu sayılır (veride işi yok)


# hâl ve yapım ekleri: "bursadan", "ankaraya", "izmirli", "konyadaki" yer adı olarak yakalansın
EKLER = ("", "da", "de", "ta", "te", "dan", "den", "tan", "ten", "a", "e", "ya", "ye", "ı", "i", "u", "ü", "yı",
         "yi", "yu", "yü", "nın", "nin", "nun", "nün", "ın", "in", "un", "ün", "lı", "li", "lu", "lü", "daki", "deki",
         "taki", "teki", "ya", "yla", "yle", "la", "le")


def kok_yer(w: str, yerler: set[str]) -> str:
    """Sözcük bir yer adı + ek mi? Kökü döner ("bursadan" → "bursa"), değilse boş."""
    for n in range(min(len(w), 12), 2, -1):
        if w[:n] in yerler and w[n:] in EKLER:
            return w[:n]
    return ""


def kelimeler(metin: str) -> list[str]:
    return re.findall(r"[a-zçğıöşüâîû]+", turkish_lower(metin))


def havuz() -> tuple[set[str], set[str], set[str]]:
    """Sentetik rehberlerdeki ad, soyad ve birim sözcükleri: verideki her ad buradan gelmeli."""
    adlar, soyadlar, birim = set(), set(), set()
    for yol in REHBERLER.values():
        for c in json.loads((ROOT / yol).read_text(encoding="utf-8"))["contacts"]:
            k = Contact.from_api(c)
            adlar.update(kelimeler(k.first_name))
            soyadlar.update(kelimeler(k.last_name))
            # kişi kayıtlarının takma adları ("Ali Yıldırım Bey") kişi adı taşır: birim sözcüğü sayılmaz
            for x in (k.department, *k.department_aliases, k.title):
                birim.update(kelimeler(x))
    return adlar, soyadlar, birim


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--veri", default=str(ROOT / "tools/egitim/veri"))
    ap.add_argument("--yasakli", default="", help="ek yasaklı adlar (satır başına bir ad)")
    ap.add_argument("--katı", action="store_true", help="bulgu varsa çıkış kodu 1")
    ap.add_argument("--cikti", default="", help="rapor JSON (boş: yazılmaz)")
    a = ap.parse_args(argv)

    yasakli = [*YASAKLI]
    if a.yasakli:
        yasakli += [turkish_lower(x.strip()) for x in Path(a.yasakli).read_text(encoding="utf-8").splitlines()
                    if x.strip()]
    adlar, soyadlar, birim = havuz()
    iller, ilceler = set(ILLER), set(ILCELER)
    # kişi adı olsa da birim / unvan adında geçen gerçek yer (ör. "Aydın Valiliği")
    birim_yer = (iller | ilceler) & birim

    bulgular: list[dict] = []
    ad_say, soyad_say, cift_say = Counter(), Counter(), Counter()
    satir = 0
    for dosya in sorted(Path(a.veri).glob("*.jsonl")):
        for no, line in enumerate(dosya.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            satir += 1
            r = json.loads(line)
            for m in r["messages"]:
                metin = m["content"]
                alan = {"system": "istem", "user": "söz", "assistant": "etiket"}[m["role"]]
                kucuk = turkish_lower(metin)
                ws = kelimeler(metin)
                def ekle(tur: str, deger: str, alan: str = alan, dosya: Path = dosya, no: int = no) -> None:
                    bulgular.append({"tur": tur, "deger": deger, "alan": alan, "dosya": dosya.name, "satir": no})

                for y in yasakli:
                    if re.search(rf"(?<![a-zçğıöşü]){re.escape(y)}(?![a-zçğıöşü])", kucuk):
                        ekle("yasakli_ad", y)
                for ad, rx in KALIPLAR.items():
                    for x in rx.findall(metin):
                        ekle(ad, x)
                for w in set(ws):
                    # "Aydın", "Yıldırım", "Fatih": havuzdaki kişi adı / soyadıysa yer sayılmaz (birim adında değilse)
                    kok = kok_yer(w, iller | ilceler)
                    if kok and (not (kok in adlar or kok in soyadlar) or kok in birim_yer):
                        ekle("gercek_yer", w)
                if alan != "etiket":
                    continue
                try:
                    d = json.loads(metin)
                except ValueError:
                    continue
                for w in kelimeler(d.get("ad", "")):
                    ad_say[w] += 1
                    if w not in adlar:
                        ekle("havuz_disi_ad", w)
                for w in kelimeler(d.get("soyad", "")):
                    soyad_say[w] += 1
                    if w not in soyadlar:
                        ekle("havuz_disi_soyad", w)
                if d.get("ad") and d.get("soyad"):
                    cift_say[f"{d['ad']} {d['soyad']}"] += 1

    print(f"{satir} satır tarandı ({a.veri})\n")
    tur_say = Counter(b["tur"] for b in bulgular)
    if not bulgular:
        print("BULGU YOK: gerçek yer, yasaklı ad, kişisel veri kalıbı ya da havuz dışı ad bulunmadı.")
    else:
        print("BULGULAR:")
        for t, n in tur_say.most_common():
            ornek = Counter(b["deger"] for b in bulgular if b["tur"] == t).most_common(8)
            print(f"  {t:18} {n:6}   örnek: {', '.join(f'{v} ({k})' for v, k in ornek)}")
    print(f"\nEnvanter: {len(ad_say)} farklı ad, {len(soyad_say)} farklı soyad, "
          f"{len(cift_say)} farklı ad+soyad çifti")
    print("  en sık çiftler:", ", ".join(f"{k} ({v})" for k, v in cift_say.most_common(5)))
    if a.cikti:
        Path(a.cikti).write_text(json.dumps({"bulgular": bulgular, "adlar": ad_say, "soyadlar": soyad_say,
                                             "ciftler": cift_say}, ensure_ascii=False, indent=1), encoding="utf-8")
    return 1 if (a.katı and bulgular) else 0


if __name__ == "__main__":
    raise SystemExit(main())
