"""TTS metni: sıra sayısı ve harf kısaltmaları (jandarma rehber örneği, 23.11.2026)."""
from __future__ import annotations

import pytest

from sesli_asistan.gateway.core.speech import ordinal_words, prepare_for_tts


@pytest.mark.parametrize("n,okunus", [(1, "birinci"), (2, "ikinci"), (3, "üçüncü"), (4, "dördüncü"), (6, "altıncı"),
                                      (9, "dokuzuncu"), (10, "onuncu"), (20, "yirminci"), (40, "kırkıncı")])
def test_sira_sayisi(n: int, okunus: str) -> None:
    assert ordinal_words(n) == okunus


@pytest.mark.parametrize("metin,okunus", [
    ("Protokol Şube Müdürlüğü 1. Protokol Kısım Amiri", "Protokol Şube Müdürlüğü birinci Protokol Kısım Amiri"),
    ("2.Cari Harekât Şube", "ikinci Cari Harekât Şube"),
    ("SSM Daire Başkanlığı", "se se me Daire Başkanlığı"),
    ("Jandarma KGYS Ağ Astsubayı", "Jandarma ka ge ye se Ağ Astsubayı"),
    ("İKK Şube Müdürü", "i ka ka Şube Müdürü"),
    ("TEM Daire Başkanı", "TEM Daire Başkanı"),            # okunabilen kısaltma sözcük gibi
    ("AFAD ve ARGE", "AFAD ve ARGE"),
    ("Sizi Ayşe ÖZTÜRK'e aktarıyorum.", "Sizi Ayşe ÖZTÜRK'e aktarıyorum."),   # büyük harfli soyadı
    ("Mehmet ÖZ", "Mehmet ÖZ"),
    ("dahili 2401", "dahili iki bin dört yüz bir"),        # sıra sayısı kuralı düz sayıya dokunmaz
    ("3,5 saat", "üç virgül beş saat"),
])
def test_tts_metni(metin: str, okunus: str) -> None:
    assert prepare_for_tts(metin) == okunus
