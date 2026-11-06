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


async def test_aktarilamaz_makam_llm_acikken_de_kurum_hattina(kamu):
    # ölçüm 06.11.2026 (qwen3.5:4b, K14): LLM'e kalınca "Ayşe Yıldız mı, Kemal Arslan mı?" (vali yardımcıları) soruldu
    agent, llm = _agent(kamu, Remote(kamu))
    t = await agent.new_session("x").handle_user("valiyle görüşmek istiyorum")
    assert [a.extension for a in t.actions] == ["1000"] and llm.calls == 0


@pytest.mark.parametrize("text", ["şu kişiyle görüşmek istiyorum", "şu an müsait misiniz"])
async def test_su_sozcugu_su_idaresine_aktarmaz(kamu, text):
    agent, _ = _agent(kamu, Remote(kamu))
    t = await agent.new_session("x").handle_user(text)
    assert all(getattr(a, "extension", "") != "7200" for a in t.actions)


async def test_birim_adinin_iyelikli_hali_teyitte_dogru_ekle(kamu):
    class Topic(Remote):
        async def find_topic(self, text):
            return {"adaylar": [{"id": next(c.id for c in kamu.contacts() if c.extension == "7200")}]}

    agent, _ = _agent(kamu, Topic(kamu))
    t = await agent.new_session("x").handle_user("su arızası bildireceğim")
    assert t.text == "Su ve Kanalizasyon İdaresine mi bağlamamı istersiniz?" and not t.actions


# --- ses benzerliği (ADR-026): STT'nin bozduğu adlar, yalnız teyitle --------------------------------------------

from sesli_asistan.gateway.core.people import sound_alike  # noqa: E402


@pytest.mark.parametrize("heard, expected", [            # ölçüm 04.11.2026, mihu, telefon / mikrofon
    ("ağlayamaz ile görüşmek istiyorum", "c-101"), ("bırakçayı arıyorum", "c-106"), ("kuba çelik", "c-106"),
    ("mehmet de mi'ye bağırar mısınız", "c-103"), ("zeynep aysan'a beraber misin", "c-105"),
    ("ayşe yamaz", "c-101"),
])
def test_ses_benzerligi_bozuk_adi_bulur(sirket, heard, expected):
    assert [a["id"] for a in sound_alike(sirket, heard)] == [expected]


@pytest.mark.parametrize("heard", [                      # ad içermeyen sözler: aday önerilmez
    "bir bilgi almak istiyorum", "saat kaça kadar açıksınız", "taş vermek istiyorum", "şifemi unuttun mu",
    "servis kanka açtırmak istiyorum", "fiyat paketi saymak istiyorum", "sevgi ne zaman çıkacak",
    "bugün hava çok güzel",
    "adres neresi", "teşekkür ederim", "alo duyuyor musunuz", "kargom gelmedi", "bayram kutlu olsun",
    "teklif almak istiyorum", "fatura itirazı", "harş vermek istiyorum", "söylemek istiyorum",
    "afiyete bağlanmak istiyorum", "ateşe girmem", "taşınma", "o pt'ye", "hayat", "ne yapıyorsunuz", "robot musunuz",
    "kimsin sen", "sesim geliyor mu", "şirketiniz nerede", "iş ilanı var mı", "faks numaranız", "kulakçayı alıyorum",
])
def test_ses_benzerligi_ad_olmayan_sozde_tetiklenmez(sirket, heard):
    assert sound_alike(sirket, heard) == []


def test_ses_benzerligi_yalniz_kurallar_bulamazsa(sirket):
    assert resolve_person(sirket, "muhasebeden ayşe hanım")["ses"] == []
    assert [a["id"] for a in resolve_person(sirket, "bırakçayı arıyorum")["ses"]] == ["c-106"]


async def test_ses_benzerligi_teyitle_aktarir(sirket):
    agent, llm = _agent(sirket, Remote(sirket))
    s = agent.new_session("x")
    t = await s.handle_user("bırakçayı arıyorum")
    assert t.actions == [] and "Burak Çelik" in t.text and llm.calls == 0
    t = await s.handle_user("evet")
    assert [a.extension for a in t.actions] == ["215"] and llm.calls == 0


async def test_ses_benzerligine_hayir_adi_yeniden_ister(sirket):
    agent, llm = _agent(sirket, Remote(sirket))
    s = agent.new_session("x")
    await s.handle_user("bırakçayı arıyorum")
    t = await s.handle_user("hayır")
    assert t.actions == [] and t.text == agent.cfg.texts.not_found      # yanlış duyulan ad: tekrar söylesin


async def test_operator_kurali_ses_benzerliginden_once(sirket):
    """D68: "operatöre" → mihu "efsane"; ses benzerliği Elif Şahin'e benzetmemeli, operatör sorulmalı."""
    agent, llm = _agent(sirket, Remote(sirket))
    t = await agent.new_session("x").handle_user("efsane bağlanmak istiyorum")
    assert "operatör" in t.text and t.actions == []
