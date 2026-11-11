"""`core.words`: "aynı sözcük mü?" kararı (sözcükler katlanmış)."""
from sesli_asistan.gateway.core.words import same_word, spoken_as


def test_ekli_bicimler_ayni_sozcuk():
    assert same_word("komutaniyla", "komutani")
    assert same_word("kaymakami", "kaymakamligi")          # kısa sözcüğün son harfi ek ünlüsü
    assert same_word("kitap", "kitabi")                     # ünsüz yumuşaması
    assert same_word("ali", "ali")


def test_ortak_basi_kisa_ya_da_ayrisan_sozcukler_ayni_degil():
    assert not same_word("satis", "satinalma")              # diyalog seti R11
    assert not same_word("yonetim", "yoneticisi")           # R14
    assert not same_word("ali", "alim")                     # 4 harften kısa ortak baş


def test_bulaniklik_yalniz_istenince():
    assert not same_word("muhasbe", "muhasebe")
    assert same_word("muhasbe", "muhasebe", fuzzy=0)


def test_spoken_as():
    assert spoken_as("muhasebedeki", "muhasebe")
    assert not spoken_as("avukat", "muhasebe")
