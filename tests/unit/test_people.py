"""Kişi çözme: akışın 2. adımı (ADR-025, MCP `kisi_coz`)."""
import json
from pathlib import Path

import pytest

from sesli_asistan.gateway.core.dialog import DialogAgent, DialogConfig
from sesli_asistan.gateway.core.models import Contact
from sesli_asistan.gateway.core.people import resolve_person
from sesli_asistan.gateway.core.ports import LlmMessage
from sesli_asistan.gateway.core.search import DirectoryIndex, has_role_word, is_role_word
from sesli_asistan.gateway.core.tools import ToolRegistry

ROOT = Path(__file__).resolve().parents[2]


def _index(name):
    data = json.loads((ROOT / name).read_text(encoding="utf-8"))
    return DirectoryIndex(Contact.from_api(c) for c in data["contacts"])


@pytest.fixture(scope="module")
def sirket():
    return _index("data/rehber_ornek.json")


@pytest.fixture(scope="module")
def kamu():
    return _index("tests/data/kamu_rehber.json")


def _r(ix, text):
    r = resolve_person(ix, text)
    return [a["id"] for a in r["adaylar"]], r["kesin"]


@pytest.mark.parametrize("text, ids, kesin", [
    ("bursa il jandarma komutanını ara", ["k-jk"], True),
    ("jandarma komutanıyla görüşmek istiyorum", ["k-jk"], True),
    ("il jandarma komutan yardımcısına bağlar mısınız", ["k-jky"], True),     # unvanın başı sonda
    ("nilüfer kaymakamı", ["k-nk"], True),
    ("kaymakamla görüşecektim", ["k-nk", "k-ik"], False),                      # iki kaymakam → hangisi
    ("emniyet müdürünü bağlar mısınız", ["k-em"], True),                        # emniyet unvanında
    ("trafik şube müdürü", ["k-trafik"], True),
    ("vali yardımcısı", ["k-vy1", "k-vy2"], False),
    ("mehmet albay", ["k-jk"], True),                                            # rütbe hitap gibi
    ("daire başkanı can bey", ["k-imarb"], True),
    ("valiyle görüşmek istiyorum", [], False),                                   # aktarılamaz makam aday olmaz
    ("emniyet müdürlüğüne bağlayın", [], False),                                 # kurum adı: birim, kişi değil
    ("jandarma komutanlığı", [], False),
])
def test_kamu(kamu, text, ids, kesin):
    got, sure = _r(kamu, text)
    assert sorted(got) == sorted(ids) and sure is kesin


@pytest.mark.parametrize("text, ids, kesin", [
    ("muhasebeden ayşe hanım", ["c-101"], True),
    ("bilgi işlemden can beyi istiyorum", ["c-104"], True),
    ("satıştan burak bey", ["c-106"], False),          # Burak muhasebede: söylenen birim tutmuyor → teyit
    ("mehmet beyi verir misiniz", ["c-103"], True),    # hitap (ekli) + tek kişi
    ("öztürk beyi arıyorum", ["c-104"], True),         # soyad
    ("mehmet var mı", ["c-103"], False),               # yalnız ad → teyit
    ("ayşe hanım", ["c-101", "c-102"], False),
    ("satış müdürüyle görüşmek istiyorum", ["c-103"], True),
    ("ben mehmet satıştan birini arıyorum", [], False),  # tanıtılan ad aday değil
    ("muhasebe", [], False), ("merhaba", [], False), ("", [], False),
])
def test_sirket(sirket, text, ids, kesin):
    got, sure = _r(sirket, text)
    assert sorted(got) == sorted(ids) and sure is kesin


@pytest.mark.parametrize("word, role", [("komutanıyla", True), ("yardımcısına", True), ("müdürünü", True),
                                         ("valiyle", True), ("komutanlığı", False), ("müdürlüğüne", False),
                                         ("kaymakamlığına", False), ("muhasebe", False)])
def test_unvan_sozcugu(word, role):
    assert has_role_word(word) is role


def test_is_role_word_kurum_eki():
    assert is_role_word("baskani") and not is_role_word("baskanligi")


class Remote:
    def __init__(self, ix, fail=False):
        self.ix, self.fail, self.asked = ix, fail, []

    async def resolve_person(self, text):
        self.asked.append(text)
        if self.fail:
            raise ConnectionError("yok")
        return resolve_person(self.ix, text)

    async def find_topic(self, text):
        return {"adaylar": []}


class Llm:
    def __init__(self):
        self.calls = 0

    async def chat(self, messages, tools):
        self.calls += 1
        return LlmMessage("Kiminle görüşmek istersiniz?")


def _agent(ix, remote):
    llm = Llm()
    return DialogAgent(llm, ToolRegistry(ix, remote=remote, remote_search=False),
                       DialogConfig(organization="D", mcp_step=True)), llm


@pytest.mark.parametrize("fail", [False, True])
async def test_unvan_llmsiz_aktarilir_mcp_dusse_de(kamu, fail):
    agent, llm = _agent(kamu, Remote(kamu, fail=fail))
    t = await agent.new_session("x").handle_user("jandarma komutanıyla görüşmek istiyorum")
    assert [a.extension for a in t.actions] == ["2001"] and llm.calls == 0
    assert agent.tools.person_failures == (1 if fail else 0)


async def test_yalniz_ad_teyit_sonra_aktar(sirket):
    agent, llm = _agent(sirket, Remote(sirket))
    s = agent.new_session("x")
    t = await s.handle_user("mehmet var mı")
    assert t.actions == [] and "Mehmet Demir" in t.text
    t = await s.handle_user("evet")
    assert [a.extension for a in t.actions] == ["230"] and llm.calls == 0


async def test_iki_aday_hangisi_ilce_ile_secim(kamu):
    agent, llm = _agent(kamu, Remote(kamu))
    s = agent.new_session("x")
    t = await s.handle_user("kaymakamla görüşecektim")
    assert "Nilüfer" in t.text and "İnegöl" in t.text
    t = await s.handle_user("nilüfer")
    assert [a.extension for a in t.actions] == ["4001"] and llm.calls == 0


async def test_olumsuzlukta_kisi_cozulmez(sirket):
    remote = Remote(sirket)
    agent, llm = _agent(sirket, remote)
    await agent.new_session("x").handle_user("mehmet bey değil başka biri")
    assert remote.asked == [] and llm.calls == 1


async def test_mcp_uydurma_kimlik_atilir(sirket):
    class Lying(Remote):
        async def resolve_person(self, text):
            return {"adaylar": [{"id": "c-108", "neden": "unvan"}], "kesin": True}     # aktarılamaz

    agent, llm = _agent(sirket, Lying(sirket))
    t = await agent.new_session("x").handle_user("genel müdür bey lütfen")
    assert t.actions == [] or all(a.extension != "100" for a in t.actions if hasattr(a, "extension"))
