"""Birim yoluyla seviye seviye daraltma (ADR-032). Rehber: data/demo_rehber.json (sentetik).

Üretici: tools/rehber/demo_rehber.py."""
import asyncio
import json
from pathlib import Path

import pytest

from sesli_asistan.gateway.composition import load_topics
from sesli_asistan.gateway.core import units
from sesli_asistan.gateway.core.dialog import DialogAgent, DialogConfig
from sesli_asistan.gateway.core.models import Contact
from sesli_asistan.gateway.core.search import DirectoryIndex
from sesli_asistan.gateway.core.tools import ToolRegistry

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def contacts():
    data = json.loads((ROOT / "data/demo_rehber.json").read_text(encoding="utf-8"))
    return [Contact.from_api(c) for c in data["contacts"]]


class NoLlm:
    async def chat(self, *a, **k):
        raise AssertionError("LLM'e gidilmemeli")


def agent(contacts):
    return DialogAgent(NoLlm(), ToolRegistry(DirectoryIndex(contacts), topics=load_topics()),
                       DialogConfig(organization="Yeşilova", llm_enabled=False, mcp_step=True))


def say(a, *texts):
    s = a.new_session("u")
    return [asyncio.run(s.handle_user(x)) for x in texts]


def _ext(contacts, *exts):
    return [c for c in contacts if c.extension in exts]


# --- saf işlevler ---

def test_birim_yolu_ve_kategori_okunur(contacts):
    c = _ext(contacts, "40111")[0]
    assert c.unit_path == ("Yeşilova İl Emniyet Müdürlüğü", "Asayiş Şube Müdürlüğü", "Cinayet Bürosu")
    assert c.category == "makam"
    assert Contact.from_api({"id": "x", "department": "Muhasebe", "extension": "1"}).unit_path == ()   # eski rehber


def test_ayrilma_seviyesi_ve_soru(contacts):
    names = units.unit_names(contacts)
    cands = _ext(contacts, "30101", "40101")                       # iki Asayiş Şube Müdürü
    assert units.split_level(cands) == 0
    assert units.question(units.groups(cands, 0), names) == "Jandarma mı, Emniyet mi?"
    buro = _ext(contacts, "40111", "40121", "40131")
    assert units.split_level(buro) == 2
    assert units.question(units.groups(buro, 2), names) == "Cinayet mi, Hırsızlık mı, Gasp mı?"
    assert units.split_level(_ext(contacts, "40111")) is None
    assert units.split_level([Contact("a", "", "", "Muhasebe", "1"), Contact("b", "", "", "Satış", "2")]) is None


def test_cevapla_suzme_seviye_ya_da_alt_birim_adiyla(contacts):
    names = units.unit_names(contacts)
    cands = _ext(contacts, "30120", "30220", "30320", "40111", "40121", "40131", "40210", "40310")   # Büro Amirleri
    assert {c.extension for c in units.narrow(cands, "emniyet", names)} == {"40111", "40121", "40131", "40210", "40310"}
    assert [c.extension for c in units.narrow(cands, "cinayet bürosu", names)] == ["40111"]       # alt birimle
    assert units.narrow(cands, "bilmiyorum", names) == []                                          # süzülmedi


# --- diyalog (demo senaryoları) ---

@pytest.mark.parametrize("texts, ext", [
    (("asayiş şube müdürü ile görüşecektim", "jandarma"), "30101"),                         # D21
    (("asayiş şube müdürüne bağlar mısınız", "emniyet"), "40101"),                          # D22
    (("nöbetçi amiri ile görüşmek istiyorum", "emniyet"), "40080"),                         # D24
    (("büro amiri ile görüşecektim", "emniyet", "cinayet"), "40111"),                       # D26
    (("mehmet kaya ile görüşecektim", "milli eğitim"), "60102"),                            # D28
    (("ayşe hanım ile görüşecektim", "trafik"), "40212"),                                   # D30
])
def test_seviye_seviye_daraltip_aktarir(contacts, texts, ext):
    turns = say(agent(contacts), *texts)
    assert all(not t.actions for t in turns[:-1]), [t.text for t in turns]
    assert [a.extension for a in turns[-1].actions] == [ext], [t.text for t in turns]


def test_kurum_cevabi_kurumun_birim_hattina_gitmez(contacts):
    # "Jandarma mı, Emniyet mi?" → "jandarma": soru adaylarından seçilir, Jandarma santraline (30000) aktarılmaz
    turns = say(agent(contacts), "asayiş şube müdürü ile görüşecektim", "jandarma")
    assert turns[0].text == "Jandarma mı, Emniyet mi?"
    assert [a.extension for a in turns[1].actions] == ["30101"]

