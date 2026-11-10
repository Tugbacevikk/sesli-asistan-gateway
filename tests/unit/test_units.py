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



@pytest.mark.parametrize("text, ext", [
    ("emniyete bağlar mısınız", "40000"),               # D06: "emniyete" → "emniye" değil "emniyet"
    ("valilik hukuk müşaviri", "20021"),                # D14: üst birim + içindeki hat tek hedef
    ("il jandarma komutanına bağlar mısınız", "30000"),  # D16: Komutan aktarılamaz → birim hattı, yardımcısı değil
])
def test_demo_tek_tur(contacts, text, ext):
    t = say(agent(contacts), text)[0]
    assert [a.extension for a in t.actions] == [ext], t.text


def test_belgegecer_istenince_birim_hatti_teyitle_sorulur(contacts):
    # D18: faks hattı aktarılamaz; birimin santraline sessizce aktarılmaz, sorulur ve "evet" ile aktarılır
    a = agent(contacts)
    t1, t2 = say(a, "emniyet faks hattı", "evet")
    assert not t1.actions and t1.text.startswith("Belgegeçer hattına sesli arama aktarılamıyor.")
    assert [x.extension for x in t2.actions] == ["40000"]


def test_sirket_rehberi_ureticiyle_ayni():
    # data/sirket_rehber.json elle değiştirilmez: üretici (sabit tohum) aynı kayıtları verir
    import importlib.util

    spec = importlib.util.spec_from_file_location("sirket_rehber", ROOT / "tools/rehber/sirket_rehber.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    data = json.loads((ROOT / "data/sirket_rehber.json").read_text(encoding="utf-8"))
    assert data["contacts"] == mod.build()
    contacts = [Contact.from_api(c) for c in data["contacts"]]
    assert len({c.extension for c in contacts}) == len(contacts)
    assert len({(c.first_name, c.last_name) for c in contacts if c.kind == "person"}) == sum(
        c.kind == "person" for c in contacts)                                  # aynı tam ad yok


# --- şirket rehberi, akışın 2. adımı (MCP_STEP; MCP yoksa aynı işlev yerelde) — canlı deneme 10.11.2026 ---------

@pytest.fixture(scope="module")
def sirket():
    data = json.loads((ROOT / "data/sirket_rehber.json").read_text(encoding="utf-8"))
    return [Contact.from_api(c) for c in data["contacts"]]


def test_kisi_cozme_ust_birimle_ve_cok_adasla(sirket):
    from sesli_asistan.gateway.core.people import resolve_person

    ix = DirectoryIndex(sirket)
    ext = {c.id: c.extension for c in sirket}
    res = resolve_person(ix, "ben tuğba, satıştaki merve hanımı arıyorum")
    assert {ext[a["id"]] for a in res["adaylar"]} == {"1230", "1244"}           # Yurt İçi / Yurt Dışı Satış
    res = resolve_person(ix, "volkan bey ile görüşecektim")
    assert len(res["adaylar"]) == 4 and not res["kesin"]                      # dördü de (birim yoluyla sorulur)


def test_mcp_adiminda_cok_adasa_birim_sorulur(sirket):
    a = DialogAgent(NoLlm(), ToolRegistry(DirectoryIndex(sirket), topics=load_topics()),
                    DialogConfig(organization="Deneme", llm_enabled=False, mcp_step=True))
    t1, t2 = say(a, "volkan bey ile görüşecektim", "ar-ge")
    assert not t1.actions and "Ar-Ge" in t1.text, t1.text
    assert [x.extension for x in t2.actions] == ["2004"], t2.text
    t1, t2 = say(a, "ben tuğba, satıştaki merve hanımı arıyorum", "yurt dışı")
    assert not t1.actions and t1.text == "Yurt İçi Satış mı, Yurt Dışı Satış mı?", t1.text
    assert [x.extension for x in t2.actions] == ["1244"], t2.text


def test_ayni_unvanli_adsiz_hatlar_sirayla_denenir():
    # S35 kararı (B, 10.11.2026): iki "Avukat" hattı ayırt edilemez → birinciye; meşgulse ikinciye; o da olmazsa
    # olağan "meşgul" cümlesi (sıra biter)
    data = json.loads((ROOT / "data/sentetik_kurum_rehber.json").read_text(encoding="utf-8"))
    a = agent([Contact.from_api(c) for c in data["contacts"]])
    s = a.new_session("s35")
    t1 = asyncio.run(s.handle_user("valilik hukuk işleri avukatı"))
    assert not t1.actions and "Hukuk Müşaviri" in t1.text and "Avukat" in t1.text, t1.text
    t2 = asyncio.run(s.handle_user("avukat"))
    assert [x.extension for x in t2.actions] == ["20025"], t2.text
    t3 = asyncio.run(s.handle_transfer_result("busy"))
    assert [x.extension for x in t3.actions] == ["20026"] and "diğer Avukat hattına" in t3.text, t3.text
    t4 = asyncio.run(s.handle_transfer_result("no_answer"))
    assert not t4.actions and t4.text, t4.text


def test_aranan_birimin_bir_kismi_yoksa_llm_aramasi_teyit_ister():
    # "jandarma sosyal tesis müdürlüğü": rehberde yok; LLM'in birim araması yalnız "jandarma"dan Jandarma santralini
    # (tek, yüksek puan) buldu ve sormadan aktarılıyordu (deneme 10.11.2026). Artık teyit sorulur.
    from sesli_asistan.gateway.core.dialog.llm import _missing_unit_words
    from sesli_asistan.gateway.core.ports import LlmMessage, ToolCall

    data = json.loads((ROOT / "data/sentetik_kurum_rehber.json").read_text(encoding="utf-8"))
    contacts = [Contact.from_api(c) for c in data["contacts"]]
    line = next(c for c in contacts if c.extension == "30000")
    assert _missing_unit_words("jandarma sosyal tesis müdürlüğü", line) == ["sosyal", "tesis"]
    assert _missing_unit_words("jandarma komutanlığı", line) == []

    class Llm:
        def __init__(self):
            self.n = 0

        async def chat(self, messages, tools):
            self.n += 1
            if self.n == 1:
                return LlmMessage("", [ToolCall(id="c1", name="InContacts",
                                                arguments={"Department": "jandarma sosyal tesis müdürlüğü"})])
            return LlmMessage("Kiminle görüşmek istersiniz?")

    a = DialogAgent(Llm(), ToolRegistry(DirectoryIndex(contacts), topics=load_topics()),
                    DialogConfig(organization="Deneme", fast_path=False, stream_replies=False))
    s = a.new_session("t")
    t = asyncio.run(s.handle_user("jandarma sosyal tesis müdürlüğüyle görüşmek istiyorum"))
    assert not t.actions, t.text
    assert s.state.confirm is not None and s.state.confirm.contact.extension == "30000", t.text
