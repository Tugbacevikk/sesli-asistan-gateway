"""Rütbe kısaltmaları (ADR-030): jandarma / emniyet rehberinde unvan kısaltmalı yazılır ("J.Kd.Bçvş."), arayan açık
söyler ("kıdemli başçavuş"). Bileşik anahtar tek tek açılımı ezer; genel listedeki eski açılımlar bozulmaz."""
from pathlib import Path

import pytest

from sesli_asistan.gateway.abbreviations import load_abbreviations

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(("rehberde", "acik"), [
    ("J.Kd.Bçvş.", "Jandarma Kıdemli Başçavuş"),
    ("J.Uzm.Çvş.", "Jandarma Uzman Çavuş"),
    ("Uzm.Çvş.", "Uzman Çavuş"),
    ("J.Asb.Kd.Bçvş.", "Jandarma Astsubay Kıdemli Başçavuş"),
    ("J.Asb.Çvş.", "Jandarma Astsubay Çavuş"),
    ("J.Onb.", "Jandarma Onbaşı"),
    ("Uzm.J.", "Uzman Jandarma"),
    ("Uzm.Erb.", "Uzman Erbaş"),
    ("Uzm.Erbş.", "Uzman Erbaş"),
    ("Tayin D.Bşk.lığı Uzm.J.Tayin Şb.Md.", "Tayin Daire Başkanlığı Uzman Jandarma Tayin Şube Müdürü"),
    ("J.Asteğm.", "Jandarma Asteğmen"),
    ("J.Tğm.", "Jandarma Teğmen"),
    ("J.Ütğm.", "Jandarma Üsteğmen"),
    ("J.Yzb.", "Jandarma Yüzbaşı"),
    ("J.Bnb.", "Jandarma Binbaşı"),
    ("J.Yb.", "Jandarma Yarbay"),
    ("J.Alb.", "Jandarma Albay"),
    ("P.Mem.", "Polis Memuru"),
    ("Kd.P.Mem.", "Kıdemli Polis Memuru"),
    ("Bkom.", "Başkomiser"),
    ("Em.Amr.", "Emniyet Amiri"),
    ("Karakol Kom. J.Kd.Çvş.", "Karakol Komutanı Jandarma Kıdemli Çavuş"),
])
def test_rutbe_acilir(rehberde: str, acik: str) -> None:
    assert load_abbreviations(extra=True).expand(rehberde) == acik


def test_tek_kisaltmalar_degismedi() -> None:
    """Rütbe dışındaki eski açılımlar aynı kalır (bileşik anahtar yalnız rütbe içinde geçerli)."""
    a = load_abbreviations(extra=True)
    assert a.expand("Kd.") == "Kıdem"
    assert a.expand("Kom.") == "Komutanı"
    assert a.expand("Bşk.Yrd.") == "Başkan Yardımcısı"


def test_emniyet_kurum_dosyasi() -> None:
    """Emniyet rehberinde "Kom." komiserdir: kurum dosyası genel listedeki "Komutanı"nı ezer."""
    a = load_abbreviations(str(ROOT / "deploy/kisaltmalar/emniyet.json"), extra=True)
    assert a.expand("Kom.Yrd.") == "Komiser Yardımcısı"
    assert a.expand("Kom.") == "Komiser"
    assert a.expand("Asayiş Şb. Kom.") == "Asayiş Şube Komiser"


def test_vhki_acilir() -> None:
    # okunuş denetimi 20.11.2026: demo rehberinde unvan "VHKİ" kalmış, TTS harf harf okuyordu
    from sesli_asistan.gateway.abbreviations import load_abbreviations

    assert load_abbreviations(extra=True).expand("VHKİ") == "Veri Hazırlama ve Kontrol İşletmeni"


@pytest.mark.parametrize("yazim,acilim", [
    ("Uzm.J.VIII.Kad.Çvş.", "Uzman Jandarma Sekizinci Kademeli Çavuş"),
    ("Uzm. J. VIII. Kad. Çvş.", "Uzman Jandarma Sekizinci Kademeli Çavuş"),
    ("J. Uzm.J.II.Kad.Çvş.", "Uzman Jandarma İkinci Kademeli Çavuş"),
    ("Uzm.J.Çvş.", "Uzman Jandarma Çavuş"),
    ("Asb.Kd.Üçvş.", "Astsubay Kıdemli Üstçavuş"),
    ("Asb. Üçvş.", "Astsubay Üstçavuş"),
    ("Jand.Uzm.IV.Kad.Çvş.", "Jandarma Uzman Dördüncü Kademeli Çavuş"),
    ("Kd. Bçvş.", "Kıdemli Başçavuş"),
    ("V.Şefi", "Vardiya Şefi"),                        # tek başına V. yine vardiya (kademe yalnız ".Kad." ile)
])
def test_uzman_jandarma_ve_ustcavus(yazim: str, acilim: str) -> None:
    # jandarma.gov.tr şehit sorgusu ve rütbe çizelgelerindeki yazımlar (23.11.2026)
    from collections import Counter

    from sesli_asistan.gateway.abbreviations import load_abbreviations

    bilinmeyen: Counter[str] = Counter()
    assert load_abbreviations(extra=True).expand(yazim, bilinmeyen) == acilim
    assert not bilinmeyen
