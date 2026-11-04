"""Akışın 2. adımı: rehber → MCP (konu → birim) → LLM (ADR-023)."""
import json
from pathlib import Path

import pytest

from sesli_asistan.gateway.core.dialog import DialogAgent, DialogConfig
from sesli_asistan.gateway.core.models import Contact
from sesli_asistan.gateway.core.ports import LlmMessage
from sesli_asistan.gateway.core.search import DirectoryIndex
from sesli_asistan.gateway.core.tools import ToolRegistry
from sesli_asistan.gateway.core.topics import Topic, TopicTable, parse_topics

ROOT = Path(__file__).resolve().parents[2]
GENEL = parse_topics(json.loads((ROOT / "src/sesli_asistan/gateway/konular.json").read_text(encoding="utf-8")))


@pytest.fixture
def index():
    data = json.loads((ROOT / "data" / "rehber_ornek.json").read_text(encoding="utf-8"))
    return DirectoryIndex(Contact.from_api(c) for c in data["contacts"])


def _ids(table, text):
    return [a["id"] for a in table.lookup(text)]


@pytest.mark.parametrize("text, hedef", [
    ("sipariş vermek istiyorum", "d-satis"), ("siparişimi takip etmek istiyorum", "d-satis"),
    ("fiyat teklifi almak istiyorum", "d-satis"), ("faturamla ilgili bir sorun var", "d-muhasebe"),
    ("ödemem yapılmadı", "d-muhasebe"), ("arızam var", "d-destek"), ("cihaz çalışmıyor", "d-destek"),
    ("şifremi unuttum", "c-104"),                       # Bilgi İşlem: birim hattı yok, tek kişi
    ("iş başvurusu yapacaktım", "c-105"),
])
def test_genel_konu_listesi_ornek_rehberde(index, text, hedef):
    assert _ids(TopicTable(index.contacts(), GENEL), text) == [hedef]


@pytest.mark.parametrize("text", ["merhaba", "ayşe yılmaz", "arizona", "kargom gelmedi", "imar durumu", ""])
def test_konu_yoksa_ya_da_birim_rehberde_yoksa_bos(index, text):
    assert TopicTable(index.contacts(), GENEL).lookup(text) == []


def test_birim_kurumun_kendi_adina_eslenir():
    contacts = [Contact("1", "", "", "Mali İşler Müdürlüğü", "500", kind="department"),
                Contact("2", "Ali", "Kaya", "Mali İşler Müdürlüğü", "501")]
    hit = TopicTable(contacts, GENEL).lookup("faturam hakkında")
    assert [a["id"] for a in hit] == ["1"] and hit[0]["birim"] == "Mali İşler Müdürlüğü"


def test_birim_hatti_yoksa_birimdeki_kisiler():
    contacts = [Contact("1", "Ali", "Kaya", "Muhasebe", "501"), Contact("2", "Can", "Ak", "Muhasebe", "502")]
    assert _ids(TopicTable(contacts, GENEL), "faturam") == ["1", "2"]      # ada göre (Ali Kaya, Can Ak)


def test_kalip_birden_cok_birime_uyuyorsa_bos():
    contacts = [Contact("1", "", "", "Muhasebe", "500", kind="department"),
                Contact("2", "", "", "Muhasebe Arşiv", "600", kind="department")]
    assert TopicTable(contacts, GENEL).lookup("faturam") == []                # hangisi belirsiz → LLM


def test_en_belirli_konu_kazanir():
    contacts = [Contact("1", "", "", "Teknik Destek", "300", kind="department"),
                Contact("2", "", "", "Su ve Kanalizasyon", "700", kind="department")]
    table = TopicTable(contacts, GENEL)
    assert _ids(table, "su arızası var") == ["2"] and _ids(table, "arıza var") == ["1"]


def test_kuruma_ozel_konu_dahiliye_gider_ve_hatali_konu_reddedilir(index):
    table = TopicTable(index.contacts(), [Topic(("poliçe",), extension="245")])
    assert _ids(table, "poliçemle ilgili") == ["c-104"]
    for bad in ({"sozler": []}, {"sozler": ["x"]}, {"sozler": ["x"], "birimler": ["a"], "dahili": "1"}):
        with pytest.raises(ValueError):
            Topic.from_dict(bad)


def test_aktarilamaz_kayit_konuyla_da_cikmaz():
    contacts = [Contact("1", "", "", "Muhasebe", "500", kind="department", transferable=False)]
    assert TopicTable(DirectoryIndex(contacts).contacts(), GENEL).lookup("faturam") == []


# --- diyalog: rehber → MCP → LLM ---

class Remote:
    def __init__(self, table=None, fail=False):
        self.table, self.fail, self.asked = table, fail, []

    async def find_topic(self, text):
        self.asked.append(text)
        if self.fail:
            raise ConnectionError("yok")
        return {"adaylar": self.table.lookup(text) if self.table else []}


class Llm:
    def __init__(self):
        self.calls = 0

    async def chat(self, messages, tools):
        self.calls += 1
        return LlmMessage("Size nasıl yardımcı olabilirim?")


def _agent(index, remote, step=True):
    llm = Llm()
    tools = ToolRegistry(index, remote=remote, remote_search=False)
    return DialogAgent(llm, tools, DialogConfig(organization="D", mcp_step=step)), llm


async def test_konu_teyitle_llmsiz_birime_aktarilir(index):
    agent, llm = _agent(index, Remote(TopicTable(index.contacts(), GENEL)))
    s = agent.new_session("x")
    t = await s.handle_user("faturamla ilgili bir sorun var")
    assert t.actions == [] and t.text == "Muhasebe'ye mi bağlamamı istersiniz?"
    t = await s.handle_user("evet")
    assert [a.extension for a in t.actions] == ["210"] and llm.calls == 0 and "Muhasebe" in t.text


async def test_konu_teyidine_hayir_aktarmaz(index):
    agent, llm = _agent(index, Remote(TopicTable(index.contacts(), GENEL)))
    s = agent.new_session("x")
    await s.handle_user("faturamla ilgili bir sorun var")
    t = await s.handle_user("hayır")
    assert t.actions == [] and t.text == "Peki. Kiminle ya da hangi birimle görüşmek istersiniz?"
    t = await s.handle_user("ayşe yılmaz ile görüşecektim")                 # bu kez ad söylendi
    assert [a.extension for a in t.actions] == ["214"]


async def test_konu_teyidinde_hayir_ve_ad(index):
    agent, llm = _agent(index, Remote(TopicTable(index.contacts(), GENEL)))
    s = agent.new_session("x")
    await s.handle_user("faturamla ilgili bir sorun var")
    t = await s.handle_user("hayır ayşe yılmaz ile görüşecektim")
    assert [a.extension for a in t.actions] == ["214"] and llm.calls == 0


async def test_konu_teyidi_kapatilabilir(index):
    llm = Llm()
    tools = ToolRegistry(index, remote=Remote(TopicTable(index.contacts(), GENEL)), remote_search=False)
    agent = DialogAgent(llm, tools, DialogConfig(organization="D", mcp_step=True, topic_confirm=False))
    t = await agent.new_session("x").handle_user("faturamla ilgili bir sorun var")
    assert [a.extension for a in t.actions] == ["210"]


async def test_rehber_once_mcp_sonra(index):
    remote = Remote(TopicTable(index.contacts(), GENEL))
    agent, llm = _agent(index, remote)
    t = await agent.new_session("x").handle_user("Ayşe Yılmaz ile görüşmek istiyorum")
    assert [a.extension for a in t.actions] == ["214"] and remote.asked == [] and llm.calls == 0


async def test_kisi_adi_gecerse_konu_adimi_atlanir(index):
    remote = Remote(TopicTable(index.contacts(), GENEL))
    agent, _ = _agent(index, remote)
    await agent.new_session("x").handle_user("faturam için burak beyi arıyorum")
    assert remote.asked == []


async def test_konu_yoksa_llm(index):
    agent, llm = _agent(index, Remote(TopicTable(index.contacts(), GENEL)))
    t = await agent.new_session("x").handle_user("bir bilgi almak istiyorum")
    assert t.actions == [] and llm.calls == 1


async def test_mcp_yoksa_llm_ve_hata_sayilir(index):
    agent, llm = _agent(index, Remote(fail=True))
    await agent.new_session("x").handle_user("faturamla ilgili bir sorun var")
    assert llm.calls == 1 and agent.tools.topic_failures == 1


async def test_mcp_uydurma_kimligi_atilir(index):
    class Lying:
        async def find_topic(self, text):
            return {"adaylar": [{"id": "uydurma"}, {"id": "c-108"}]}         # c-108: aktarılamaz (genel müdür)

    agent, llm = _agent(index, Lying())
    t = await agent.new_session("x").handle_user("faturamla ilgili bir sorun var")
    assert t.actions == [] and llm.calls == 1


async def test_adim_kapaliyken_mcpye_sorulmaz(index):
    remote = Remote(TopicTable(index.contacts(), GENEL))
    agent, llm = _agent(index, remote, step=False)
    await agent.new_session("x").handle_user("faturamla ilgili bir sorun var")
    assert remote.asked == [] and llm.calls == 1
