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
