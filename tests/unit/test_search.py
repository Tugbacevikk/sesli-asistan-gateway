import json
from pathlib import Path

import pytest

from sesli_asistan.gateway.core.models import Contact, Match
from sesli_asistan.gateway.core.search import DirectoryIndex, number_to_words, search_key

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def index() -> DirectoryIndex:
    data = json.loads((ROOT / "data/rehber_ornek.json").read_text(encoding="utf-8"))
    return DirectoryIndex(Contact.from_api(c) for c in data["contacts"])


def _names(r):
    return [c.contact.full_name for c in r.candidates]


@pytest.mark.parametrize("n,expected", [(0, "sıfır"), (7, "yedi"), (14, "on dört"), (100, "yüz"),
                                        (214, "iki yüz on dört"), (1000, "bin"), (2026, "iki bin yirmi altı")])
def test_sayi_okunusu(n, expected):
    assert number_to_words(n) == expected


def test_arama_anahtari():
    assert search_key("Dr. Ayşe Hanım'ı") == "ayse"
    assert search_key("İNSAN KAYNAKLARI") == "insan kaynaklari"
    assert search_key("100 numara") == "yuz numara"
    assert search_key("zeynep hanımı") == "zeynep"


@pytest.mark.parametrize("name,dept,match,first", [
    ("Ayşe Hanım", "muhasebeden", Match.SINGLE_HIGH, "Ayşe Yılmaz"),   # hitap + hâl eki
    ("ayse yilmaz", "", Match.SINGLE_HIGH, "Ayşe Yılmaz"),             # Türkçe karakter yok
    ("", "finans", Match.SINGLE_HIGH, "Muhasebe"),                      # birim eş anlamlısı → birim hattı
    ("", "satıştan", Match.SINGLE_HIGH, "Satış"),
    ("", "ik", Match.SINGLE_HIGH, "Zeynep Arslan"),                     # kısaltma; "teknik" ile eşleşmez
    ("satış müdürü", "", Match.SINGLE_HIGH, "Mehmet Demir"),            # unvan
    ("burak çeliği", "", Match.SINGLE_HIGH, "Burak Çelik"),
    ("ahmet", "", Match.SINGLE_LOW, "Mehmet Demir"),                    # yakın ama emin değil → onay
])
def test_eslesme(index, name, dept, match, first):
    r = index.search(name, dept)
    assert r.match == match
    assert _names(r)[0] == first


def test_birden_cok_ayse(index):
    r = index.search("ayşe")
    assert r.match == Match.MULTIPLE
    assert set(_names(r)) == {"Ayşe Yılmaz", "Ayşe Kaya"}
    assert index.search("ayşe", "satış").match == Match.SINGLE_HIGH


def test_yalniz_soyad_uzun_sorguyu_tam_puanlamaz(index):
    # "aysel yilmaz" sorgusu yalnızca soyad alanıyla tam puan almamalı (yanlış kişiye kesin aktarma)
    r = index.search("aysel yılmaz")
    assert r.candidates[0].score < 100


def test_aktarilamaz_ve_silinmis_gorunmez(index):
    assert index.search("hakan koç").match == Match.NONE          # transferable=false
    assert index.search("genel müdür").match == Match.NONE
    assert index.search("selin aydın").match == Match.NONE        # deleted
    assert index.search("", "", "100").match == Match.NONE        # dahiliyle de bulunmaz


def test_dahili_ile_arama(index):
    r = index.search(extension="245")
    assert r.match == Match.SINGLE_HIGH and r.candidates[0].contact.full_name == "Can Öztürk"
    assert index.search(extension="2 1 4").candidates[0].contact.extension == "214"


def test_bos_sorgu(index):
    assert index.search().match == Match.NONE


def test_ipucu_kelimeleri(index):
    hw = index.hotwords()
    assert "Muhasebe" in hw and "Ayşe Yılmaz" in hw and "Hakan Koç" not in hw
    assert len(hw) == len(set(hw))


def test_ipucu_uzunluk_siniri():
    # Whisper istemi en çok 223 belirteç: büyük rehberde ipuçları harf bütçesiyle kesilir, birimler önce
    people = [Contact.from_api({"id": str(i), "first_name": f"Kişi{i:04d}", "last_name": "Uzunsoyadlıoğlu",
                                "department": f"Birim{i % 5}", "extension": str(1000 + i)}) for i in range(500)]
    hw = DirectoryIndex(people).hotwords()
    assert sum(len(w) + 1 for w in hw) <= 400
    assert hw[:5] == [f"Birim{i}" for i in range(5)] and len(hw) > 5



def test_ipucu_onceligi_adaylar_sonra_sik_arananlar():
    # Büyük rehberde alfabede sonda kalan adlar sığmaz; sık arananlar ve son aramanın adayları öne alınır
    people = [Contact.from_api({"id": str(i), "first_name": f"Kişi{i:04d}", "last_name": "Uzunsoyadlıoğlu",
                                "department": f"Birim{i % 5}", "extension": str(1000 + i)}) for i in range(500)]
    idx = DirectoryIndex(people)
    assert "Kişi0499 Uzunsoyadlıoğlu" not in idx.hotwords()
    hw = idx.hotwords(priority=["1499", "1498", "9999"])
    assert hw[:2] == ["Kişi0499 Uzunsoyadlıoğlu", "Kişi0498 Uzunsoyadlıoğlu"] and hw[2] == "Birim0"
    focus = idx.search("kişi 0321").candidates
    hw = idx.hotwords(priority=["1499"], focus=focus)
    assert focus and hw[:2] == [focus[0].contact.full_name, focus[0].contact.department]
    first_other = next(i for i, w in enumerate(hw) if w == "Kişi0499 Uzunsoyadlıoğlu")
    assert {c.contact.full_name for c in focus} <= set(hw[:first_other])     # adaylar, sık arananlardan önce
    assert sum(len(w) + 1 for w in hw) <= 400 and len(hw) == len(set(hw))


# Whisper'ın telefon sesinde tipik hataları: Türkçe harf kaybı, ek, harf değişimi, kelime bölünmesi
@pytest.mark.parametrize("name,dept,ext", [
    ("ayse yilmaz", "", "214"), ("aişe yılmaz", "", "214"), ("ayşe yılmas", "", "214"),
    ("can öz türk", "", "245"), ("canöztürk", "", "245"), ("zeynep aslan", "", "260"),
    ("zeynap arslan", "", "260"), ("burak çelikle", "", "215"), ("elif şahini", "", "301"),
    ("mehmet demire", "", "230"), ("mehmet temir", "", "230"), ("ayşe hanım", "muhasebe", "214"),
    ("", "insan kaynaklarına", "260"), ("", "teknik destekten", "300"), ("", "satıştan", "229"),
])
def test_stt_hatalarinda_net_eslesme(index, name, dept, ext):
    r = index.search(name=name, department=dept)
    assert r.match == Match.SINGLE_HIGH and r.candidates[0].contact.extension == ext


@pytest.mark.parametrize("name", ["mehmet demirci", "elif şahinler", "canan", "öztürkler"])
def test_uzamis_ad_aktarilmadan_sorulur(index, name):
    # "Demirci" başka bir soyadı: Mehmet Demir'e kendiliğinden aktarılmamalı (ADR-012), sorulmalı
    assert index.search(name=name).match in (Match.SINGLE_LOW, Match.MULTIPLE)


def test_takma_adin_tam_hali_hitabiyla_tek_kisiyi_bulur(index):
    # "Ayşe Abla" arama anahtarında "ayse"ye iner; tam söylenince yalnızca takma adın sahibi (27.10.2026 deneme)
    idx = DirectoryIndex(index.contacts())
    idx.set_extra_aliases({"c-101": ["Ayşe Abla"]})
    r = idx.search("Ayşe abla")
    assert r.match == Match.SINGLE_HIGH and r.candidates[0].contact.extension == "214"
    assert idx.search("ayşe ablaya").candidates[0].contact.extension == "214"       # ek
    assert idx.search("ayşe abla lütfen").match == Match.SINGLE_HIGH                # cümle içinde
    assert idx.search("Ayşe").match == Match.MULTIPLE                              # hitapsız: yine iki Ayşe
    assert idx.search("Ayşe hanım").match == Match.MULTIPLE
    idx.set_extra_aliases({"c-101": ["Ayşe Abla"], "c-102": ["ayşe abla"]})          # iki kişide aynı takma ad
    assert idx.search("Ayşe abla").match == Match.MULTIPLE


def _reference_scores(idx, name, dept):
    """Eski kayıt-kayıt puanlama (hızlandırmanın doğruluk referansı)."""
    from sesli_asistan.gateway.core.search import _best, query_variants
    qn, qd = search_key(name), search_key(dept)
    vn, vd = query_variants(qn), query_variants(qd)
    out = {}
    for c, nk, dk in idx._entries:
        parts = ([_best(vn, nk)] if qn else []) + ([_best(vd, dk, subset=True)] if qd else [])
        if parts and min(parts) >= idx.min_score:
            out[c.id] = min(parts)
    return out


def test_toplu_puanlama_eski_yontemle_ayni():
    import random
    rng = random.Random(5)
    first = ["Ayşe", "Ayşegül", "Mehmet", "Can", "Canan", "Zeynep", "Elif", "Burak", "Demirci"]
    last = ["Yılmaz", "Kaya", "Demir", "Demirci", "Çelik", "Şahin", "Öztürk", "Arslan"]
    depts = ["Muhasebe", "Satış", "İnsan Kaynakları", "Bilgi İşlem", "Teknik Destek", "Ar-Ge"]
    people = [Contact(str(i), rng.choice(first), rng.choice(last), rng.choice(depts), str(1000 + i),
                      title=rng.choice(["Satış Müdürü", "Muhasebe Uzmanı", ""]),
                      aliases=tuple(rng.sample(["Ayşe Hanım", "Mehmet Bey", "IT"], k=rng.randint(0, 1))),
                      department_aliases=tuple(rng.sample(["Finans", "Destek", "Personel"], k=rng.randint(0, 1))))
              for i in range(300)]
    people.append(Contact("d1", "", "", "Muhasebe", "210", kind="department"))
    idx = DirectoryIndex(people)
    words = ("ayşe yılmaz mehmet demir demirci ayşegül muhasebe satış finans destek it can canan şahin müdürü "
             "uzmanı lütfen kaynaklarına bilgi işlem teknik arıza ar ge zeynep aslan").split()
    for _ in range(400):
        name = " ".join(rng.sample(words, rng.randint(0, 3)))
        dept = " ".join(rng.sample(words, rng.randint(1, 2))) if rng.random() < 0.4 else ""
        got = {c.contact.id: c.score for c in idx._search(search_key(name), search_key(dept)).candidates}
        ref = _reference_scores(idx, name, dept)
        top = sorted(ref.items(), key=lambda x: -x[1])
        assert all(ref[k] == v for k, v in got.items()), (name, dept)
        if top:
            assert got, (name, dept)


def test_arama_onbellegi_rehber_degisince_temizlenir(index):
    idx = DirectoryIndex(index.contacts())
    assert idx.search("ferhat yıldırım").match == Match.NONE
    idx.set_extra_aliases({"c-101": ["Ferhat Yıldırım"]})
    assert idx.search("ferhat yıldırım").match == Match.SINGLE_HIGH
