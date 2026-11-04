"""Santral dil modeli için cümle derlemi (ADR-028): arayanın söyleyebileceği cümleler, STT'nin yazdığı biçimde
(küçük harf, Türkçe harfler, özel adda kesme işareti: "ayşe yılmaz'a").

    python tools/stt/dil_modeli/derlem.py --cikti derlem.txt
    python tools/stt/dil_modeli/derlem.py --rehber data/rehber_ornek.json --rehber tests/data/kamu_rehber.json \\
        --cikti derlem.txt --adet 60000

Kaynaklar (hepsi kurumdan bağımsız ya da kurumun kendi rehberi; kişisel ses / metin yok):
  - Türkiye'de yaygın ad ve soyadlar (adlar.txt): kurum rehberinden bağımsız ad dağılımı,
  - verilen rehber(ler): adlar, birimler (ve eş anlamlıları), unvanlar — kurum kurulurken kendi rehberiyle üretilir,
  - genel konu listesi (gateway/konular.json), kamu ve şirket unvanları, dahili numaralar (yazıyla ve rakamla),
  - evet / hayır / operatör / selam / kapanış ve diyalog setindeki arayan sözleri (tests/data/*.yaml).
Çıktı satır başına bir cümle, karışık sırada, tekrarlar sınırlı. Rastgelelik tohumlu: aynı girdi → aynı derlem.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
from sesli_asistan.gateway.core.search import number_to_words  # noqa: E402
from sesli_asistan.libs.text.normalizer import turkish_lower  # noqa: E402

_BACK, _FRONT = set("aıou"), set("eiöü")


def _last_vowel(word: str) -> str:
    return next((ch for ch in reversed(turkish_lower(word)) if ch in _BACK | _FRONT), "e")


def ek(name: str, kind: str, apostrophe: bool = True) -> str:
    """Hâl ekleri (ünlü uyumu, kaynaştırma, sertleşme): yönelme "a", belirtme "ı", birliktelik "la", ayrılma "dan",
    bulunma "da". Özel adda kesme işaretiyle ("ayşe yılmaz'a"), cins adda bitişik ("muhasebeye")."""
    w = name.strip()
    low = turkish_lower(w)
    v = _last_vowel(w)
    back = v in _BACK
    ends_vowel = low[-1:] in _BACK | _FRONT
    hard = low[-1:] in set("fstkçşhp")
    # iyelik ekli tamlama ("daire başkanı", "insan kaynakları"): -n- kaynaştırma → başkanına, başkanını, başkanından
    possessive = not apostrophe and " " in w and low[-1:] in set("ıiuü")
    buf = "n" if possessive else ("y" if ends_vowel else "")
    if kind == "a":
        s = buf + ("a" if back else "e")
    elif kind == "ı":
        s = buf + {"a": "ı", "ı": "ı", "o": "u", "u": "u", "e": "i", "i": "i", "ö": "ü", "ü": "ü"}[v]
    elif kind == "la":
        s = ("y" if ends_vowel else "") + ("la" if back else "le")
    elif kind in ("dan", "da"):
        s = ("n" if possessive else "") + ("t" if hard and not possessive else "d") + ("a" if back else "e") \
            + ("n" if kind == "dan" else "")
    else:
        raise ValueError(kind)
    return f"{w}'{s}" if apostrophe else w + s


def read_names(path: Path) -> tuple[list[tuple[str, str]], list[str]]:
    """→ ([(ad, "bey" | "hanım")], [soyad])."""
    first: list[tuple[str, str]] = []
    last: list[str] = []
    section = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line in ("erkek:", "kadın:", "soyad:"):
            section = line[:-1]
            continue
        if section == "soyad":
            last.append(line)
        elif section:
            first.append((line, "bey" if section == "erkek" else "hanım"))
    return first, last


PUBLIC_TITLES = ["vali", "vali yardımcısı", "kaymakam", "il jandarma komutanı", "jandarma komutanı",
                 "il emniyet müdürü", "emniyet müdürü", "il sağlık müdürü", "il milli eğitim müdürü",
                 "belediye başkanı",
                 "başkan yardımcısı", "daire başkanı", "şube müdürü", "genel sekreter", "rektör", "dekan",
                 "başhekim", "müdür yardımcısı", "komutan yardımcısı", "asayiş şube müdürü", "trafik şube müdürü"]
COMPANY_TITLES = ["genel müdür", "genel müdür yardımcısı", "satış müdürü", "muhasebe müdürü", "insan kaynakları müdürü",
                  "satış uzmanı", "muhasebe uzmanı", "destek uzmanı", "sistem yöneticisi", "proje yöneticisi",
                  "satın alma sorumlusu", "kalite sorumlusu", "fabrika müdürü", "üretim şefi", "sekreter"]
DEPARTMENTS = ["muhasebe", "finans", "satış", "pazarlama", "teknik destek", "müşteri hizmetleri", "insan kaynakları",
               "bilgi işlem", "satın alma", "lojistik", "kalite", "hukuk", "üretim", "ar-ge", "halkla ilişkiler",
               "yazı işleri", "özel kalem", "valilik", "kaymakamlık", "zabıta", "imar", "nüfus müdürlüğü",
               "sağlık müdürlüğü", "emniyet", "jandarma", "fen işleri", "temizlik işleri", "sosyal yardım"]
RANKS = ["albay", "yarbay", "binbaşı", "yüzbaşı", "üsteğmen", "teğmen", "astsubay", "komiser", "başkomiser"]
HONORIFICS = ["bey", "hanım"]
FILLERS = ["", "", "", "merhaba ", "iyi günler ", "alo ", "kolay gelsin ", "pardon "]
POLITE = ["", "", " lütfen", " rica ediyorum"]

PERSON = ["{x} ile görüşmek istiyorum", "{x_la} görüşmek istiyorum", "{x_ı} arıyorum", "{x_a} bağlar mısınız",
          "{x_a} bağlayın", "{x} orada mı", "{x} müsait mi", "{x} var mı", "{x_la} görüşebilir miyim",
          "{x_ı} verir misiniz", "{x}", "{x} lütfen", "{x_a} ulaşmak istiyorum", "{x_la} görüşecektim",
          "{x_ı} aramıştım", "{x_a} aktarır mısınız"]
UNIT = ["{u_a} bağlar mısınız", "{u} ile görüşmek istiyorum", "{u} lütfen", "{u}", "{u_dan} biriyle görüşecektim",
        "{u_ı} arıyorum", "{u_a} bağlayın", "{u_dan} birini istiyorum", "{u} birimi lütfen"]
TOPIC = ["{t} ile ilgili arıyorum", "{t} hakkında bilgi almak istiyorum", "{t} için arıyorum", "{t} konusunda",
         "{t} ile ilgili bir sorun var"]
SHORT = ["evet", "evet lütfen", "evet doğru", "tamam", "olur", "aynen", "hayır", "hayır değil", "yok", "istemiyorum",
         "operatör", "operatöre bağlayın", "operatörle görüşmek istiyorum", "beni operatöre bağlar mısınız",
         "bir yetkiliyle görüşmek istiyorum", "merhaba", "alo", "iyi günler", "selam", "teşekkür ederim",
         "görüşmeyi sonlandırmak istiyorum", "kapatabilirsiniz", "başka bir şey yok", "anlamadım tekrar eder misiniz",
         "ilki", "ikincisi", "sonuncusu", "hiçbiri", "bilmiyorum", "mesai saatleri nedir", "saat kaça kadar açıksınız",
         "adresiniz neresi", "sipariş vermek istiyorum", "fiyat teklifi almak istiyorum", "şikayetim var",
         "faturamla ilgili bir sorun var", "arızam var", "şifremi unuttum", "iş başvurusu yapacaktım"]


def generate(n: int, first: list[tuple[str, str]], last: list[str], directory: list[dict], topics: list[str],
             extra: list[str], seed: int = 7) -> list[str]:
    rnd = random.Random(seed)
    persons = [(c.get("first_name", ""), c.get("last_name", ""), c.get("title", ""), c.get("department", ""))
               for c in directory if c.get("kind", "person") == "person" and c.get("first_name")]
    units = sorted({*DEPARTMENTS, *(turkish_lower(c.get("department", "")) for c in directory if c.get("department")),
                    *(turkish_lower(a) for c in directory for a in c.get("department_aliases") or [])} - {""})
    titles = sorted({*PUBLIC_TITLES, *COMPANY_TITLES,
                     *(turkish_lower(c.get("title", "")) for c in directory if c.get("title"))} - {"", "birim hattı"})

    gender = dict(first)

    def person() -> str:
        r = rnd.random()
        if persons and r < 0.25:
            f, s, *_ = rnd.choice(persons)
        else:
            f, s = rnd.choice(first)[0], rnd.choice(last)
        hon = gender.get(f) or rnd.choice(HONORIFICS)          # rehberdeki tanınmayan ad: hitap rastgele
        r = rnd.random()
        if r < 0.45:
            return f"{f} {s}"
        if r < 0.65:
            return f"{f} {hon}"
        if r < 0.75:
            return f"{s} {hon}"
        if r < 0.82:
            return rnd.choice([f"{rnd.choice(RANKS)} {f} {s}", f"{f} {rnd.choice(RANKS)}"])
        if r < 0.92:
            return f
        return f"{rnd.choice(titles)} {f} {s}"

    def fill(tpl: str, key: str, value: str, proper: bool) -> str:
        return tpl.format(**{key: value, f"{key}_a": ek(value, "a", proper), f"{key}_ı": ek(value, "ı", proper),
                             f"{key}_la": ek(value, "la", proper), f"{key}_dan": ek(value, "dan", proper)})

    out: list[str] = []
    while len(out) < n:
        r = rnd.random()
        if r < 0.45:
            s = fill(rnd.choice(PERSON), "x", person(), True)
        elif r < 0.6:
            s = fill(rnd.choice(PERSON), "x", rnd.choice(titles), False)
        elif r < 0.72:
            s = fill(rnd.choice(UNIT), "u", rnd.choice(units), False)
        elif r < 0.75:
            s = fill(rnd.choice(UNIT), "u", f"{rnd.choice(['nilüfer', 'inegöl', 'osmangazi', 'yıldırım', 'gemlik'])} "
                                            "kaymakamlığı", False)
        elif r < 0.84 and topics:
            s = rnd.choice(TOPIC).format(t=rnd.choice(topics))
        elif r < 0.9:
            num = rnd.randint(100, 999)
            s = rnd.choice([f"{number_to_words(num)} numaraya bağlayın", f"dahili {num}",
                            f"{number_to_words(num)} lütfen", f"{num} numaralı dahili",
                            " ".join(number_to_words(int(d)) for d in str(num))])
        elif r < 0.96:
            s = rnd.choice(SHORT)
        elif extra:
            s = rnd.choice(extra)
        else:
            continue
        request = r < 0.84                                   # kişi / unvan / birim / konu isteği
        s = rnd.choice(FILLERS) + s + (rnd.choice(POLITE) if request and not s.endswith("lütfen") else "")
        out.append(" ".join(turkish_lower(s).split()))
    counts = Counter(out)
    rnd.shuffle(out)
    seen: Counter[str] = Counter()
    limited = []
    for s in out:                    # sık kısa sözler ("evet") derlemi boğmasın: cümle başına en çok 50 tekrar
        if seen[s] < 50:
            limited.append(s)
            seen[s] += 1
    del counts
    return limited


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rehber", type=Path, action="append", default=[], help="rehber JSON (birden çok verilebilir)")
    ap.add_argument("--adlar", type=Path, default=Path(__file__).with_name("adlar.txt"))
    ap.add_argument("--konular", type=Path, default=ROOT / "src/sesli_asistan/gateway/konular.json")
    ap.add_argument("--set", type=Path, action="append", default=[], help="diyalog seti YAML (arayan sözleri)")
    ap.add_argument("--adet", type=int, default=60000)
    ap.add_argument("--tohum", type=int, default=7)
    ap.add_argument("--cikti", type=Path, required=True)
    a = ap.parse_args(argv)
    first, last = read_names(a.adlar)
    directory = [c for p in a.rehber for c in json.loads(p.read_text(encoding="utf-8")).get("contacts", [])
                 if not c.get("deleted")]
    topics = [turkish_lower(w) for t in json.loads(a.konular.read_text(encoding="utf-8"))["konular"]
              for w in t["sozler"]]
    extra: list[str] = []
    sets = a.set or [ROOT / "tests/data/diyalog_seti.yaml", ROOT / "tests/data/diyalog_seti_kamu.yaml"]
    try:
        import yaml

        for p in sets:
            for sc in yaml.safe_load(p.read_text(encoding="utf-8")) or []:
                extra += [x if isinstance(x, str) else x.get("arayan", "") for x in sc.get("konusma", [])
                          if isinstance(x, str) or "arayan" in x]
    except ImportError:
        pass
    extra = [turkish_lower(x).replace("[santral]", "").strip() for x in extra if x and "[" not in x]
    lines = generate(a.adet, first, last, directory, topics, extra, a.tohum)
    a.cikti.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{a.cikti}: {len(lines)} cümle, {len(set(lines))} farklı, {sum(len(x.split()) for x in lines)} sözcük")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
