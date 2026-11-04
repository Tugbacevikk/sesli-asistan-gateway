"""Santral dil modeli derlemi (ADR-028): Türkçe ekler, cinsiyet uyumu, derlem kuralları (torch gerekmez)."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/stt/dil_modeli"))
from derlem import ek, generate, read_names  # noqa: E402


@pytest.mark.parametrize("word, kind, proper, expected", [
    ("ayşe yılmaz", "a", True, "ayşe yılmaz'a"), ("burak", "ı", True, "burak'ı"), ("mehmet", "la", True, "mehmet'le"),
    ("ayşe", "a", True, "ayşe'ye"), ("koç", "dan", True, "koç'tan"), ("muhasebe", "a", False, "muhasebeye"),
    ("satış", "dan", False, "satıştan"), ("genel müdür", "ı", False, "genel müdürü"),
    ("daire başkanı", "ı", False, "daire başkanını"), ("belediye başkanı", "a", False, "belediye başkanına"),
    ("insan kaynakları", "dan", False, "insan kaynaklarından"), ("vali yardımcısı", "la", False, "vali yardımcısıyla"),
])
def test_turkce_hal_ekleri(word, kind, proper, expected):
    assert ek(word, kind, proper) == expected


def test_adlar_cinsiyetli_ve_derlem_kurallari():
    first, last = read_names(ROOT / "tools/stt/dil_modeli/adlar.txt")
    gender = dict(first)
    assert gender["Mehmet"] == "bey" and gender["Zeynep"] == "hanım" and "Yılmaz" in last
    lines = generate(4000, first, last, [], ["fatura"], ["merhaba"], seed=1)
    text = "\n".join(lines)
    for wrong in ("zeynep bey", "mehmet hanım", "ayşe bey", "ahmet hanım", "başkanıyı", "başkanıya",
                  "evet rica ediyorum", "hayır lütfen"):
        assert wrong not in text, wrong
    assert all(x == x.strip() and "  " not in x and x == x.lower() for x in lines)
    assert max(lines.count(x) for x in set(lines)) <= 50                  # sık kısa sözler derlemi boğmaz
    assert generate(500, first, last, [], ["fatura"], [], seed=3) == generate(500, first, last, [], ["fatura"], [],
                                                                               seed=3)   # tohumlu: tekrarlanabilir
