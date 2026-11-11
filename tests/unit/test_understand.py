"""ADR-037 "LLM gerektiğinde anlar" kipi: sahte LLM'le (yanıt söze göre). Gerçek modelle karşılaştırma:
tests/eval/anlama_karsilastir.py."""
import json
from pathlib import Path

import pytest

from sesli_asistan.gateway.core.dialog import DialogAgent, DialogConfig
from sesli_asistan.gateway.core.dialog.understand import CANONICAL, Understanding
from sesli_asistan.gateway.core.models import Contact
from sesli_asistan.gateway.core.phrases import (
    asks_general_help,
    asks_office_hours,
    asks_operator,
    is_greeting_only,
    wants_anyone,
    wants_hangup,
)
from sesli_asistan.gateway.core.ports import LlmError, LlmMessage
from sesli_asistan.gateway.core.search import DirectoryIndex
from sesli_asistan.gateway.core.tools import ToolRegistry

ROOT = Path(__file__).resolve().parents[2]


def _index():
    data = json.loads((ROOT / "data/sirket_rehber.json").read_text(encoding="utf-8"))
    return DirectoryIndex(Contact.from_api(c) for c in data["contacts"])


class MapLlm:
    """Arayanın sözüne göre hazır JSON döner; sözlükte yoksa hata (LLM düştü)."""

    def __init__(self, answers: dict[str, dict]):
        self.answers, self.calls = answers, 0

    async def chat(self, messages, tools):
        self.calls += 1
        said = messages[-1]["content"]
        if said not in self.answers:
            raise LlmError("yok")
        return LlmMessage(json.dumps(self.answers[said], ensure_ascii=False))


def _agent(answers):
    llm = MapLlm(answers)
    return DialogAgent(llm, ToolRegistry(_index()), DialogConfig(understand="llm")), llm


def test_kalip_sozler_kural_adimlarinca_taninir():
    # kalıp söz kural adımlarının kesin tanıdığı biçimde olmalı: tanınmazsa kip sessizce yanlış yola gider
    assert asks_operator(CANONICAL["operator"])
    assert wants_hangup(CANONICAL["kapat"], strict=True)
    assert is_greeting_only(CANONICAL["selam"])
    assert asks_office_hours(CANONICAL["mesai"])
    assert asks_general_help(CANONICAL["bilgi"])
    assert wants_anyone(CANONICAL["farketmez"])


def test_cozumleme_bozuk_ve_eksik_json():
    assert Understanding.parse("") is None
    assert Understanding.parse("tamam") is None
    assert Understanding.parse('{"niyet": "uçmak"}') is None
    u = Understanding.parse('Elbette: {"niyet": "aktar", "birim": "Muhasebe", "unvan": "müdür", "sira": "x"}')
    assert u is not None and u.sira == 0
    assert u.canonical() == "muhasebe müdür"
    assert Understanding("aktar", dahili="1401 numara").canonical() == "1401"
    assert Understanding("aktar", sira=2).canonical() == "ikincisi"
    assert Understanding("aktar").canonical() is None
    assert Understanding("belirsiz").canonical() is None


async def test_kuralin_cozdugu_soz_llme_gitmez():
    a, llm = _agent({})
    s = a.new_session("u0")
    s.greeting()
    turn = await s.handle_user("muhasebeye bağlar mısınız")
    assert [x.extension for x in turn.actions if x.type == "transfer"] == ["1400"]
    assert llm.calls == 0


async def test_kaniti_zayif_soz_once_llme_gider_ve_kalipla_cozulur():
    # kural "muhasebe"yi görüp birim hattına aktarırdı (ölçüm 2, F01); artan sözcükler LLM'e yönlendirir
    said = "muhasebenin başındaki kişi kimse onunla görüşmek istiyorum"
    a, llm = _agent({said: {"niyet": "aktar", "unvan": "müdür", "birim": "muhasebe"}})
    s = a.new_session("u1")
    s.greeting()
    turn = await s.handle_user(said)
    assert [x.extension for x in turn.actions if x.type == "transfer"] == ["1401"]
    assert llm.calls == 1                       # tek "anla" çağrısı; araçlı LLM döngüsü yok


def test_uydurma_ve_atlanan_ad_reddedilir():
    names = frozenset({"merve", "ayse"})
    invented = Understanding("aktar", birim="Eğitim ve Çocuk Bakımı")
    assert invented.problem("kreş birimine bağlar mısınız", names)             # ölçüm 2, R18
    dropped = Understanding("aktar", birim="kalite", hitap="hanım")              # ölçüm 2, R10: ad atlandı
    assert dropped.missing_names("kaliteden merve hanımla görüşecektim", names) == ["merve"]
    other = Understanding("aktar", ad="ayşe", birim="kalite")                    # başka ad: reddedilir
    assert other.problem("kaliteden merve hanımla görüşecektim", names)
    ok = Understanding("aktar", ad="merve", hitap="hanım", birim="kalite")
    assert ok.problem("kaliteden merve hanımla görüşecektim", names) is None
    told = Understanding("aktar", unvan="asistan", birim="genel müdür")      # unvan eş anlamlı olabilir
    assert told.problem("genel müdürün sekreterine bağlar mısınız", names) is None
    assert Understanding("aktar", dahili="1234").problem("bin dört yüz bire bağlayın", names)
    assert Understanding("aktar", ad="can").problem("ben ayşe, can beye bağlayın", names) is None


def test_artan_sozcuk_olcusu():
    a, _ = _agent({})
    assert a.unexplained("muhasebeye bağlar mısınız") == []
    assert a.unexplained("kaliteden merve hanımla görüşecektim") == []
    assert len(a.unexplained("muhasebenin başındaki kişi kimse onunla görüşmek istiyorum")) >= 2


async def test_hitap_kalipta_korunur():
    u = Understanding.parse('{"niyet": "aktar", "ad": "Merve", "hitap": "hanım", "birim": "muhasebe"}')
    assert u is not None and u.canonical() == "muhasebe merve hanım"
    assert Understanding("aktar", ad="merve", soyad="öztürk", hitap="hanım").canonical() == "merve öztürk"


async def test_konudan_birim_teyitle_sorulur_cevap_llme_gitmez():
    said = "e-postalarım gelmiyor"
    a, llm = _agent({said: {"niyet": "konu", "konu": "e-posta", "tahmini_birim": "Teknik Destek"}})
    s = a.new_session("u2")
    s.greeting()
    asked = await s.handle_user(said)
    assert not asked.actions and "Teknik Destek" in asked.text
    done = await s.handle_user("evet")          # açık sorunun cevabı kurala gider
    assert [x.extension for x in done.actions if x.type == "transfer"] == ["1760"]
    assert llm.calls == 1


async def test_llm_dusunce_kural_turu_cevap_verir():
    a, llm = _agent({})                         # her çağrıda hata
    s = a.new_session("u3")
    s.greeting()
    turn = await s.handle_user("o işlere bakan yerle görüşecektim")
    assert not [x for x in turn.actions if x.type == "transfer"]
    assert llm.calls == 1


@pytest.mark.parametrize("said, niyet, action", [
    ("ya ben en iyisi gerçek bir insanla konuşayım", "operator", "operator"),
    ("tamam tamam gerek kalmadı ben sonra ararım", "kapat", "hangup"),
])
async def test_niyetler(said, niyet, action):
    a, _ = _agent({said: {"niyet": niyet}})
    s = a.new_session("u4")
    s.greeting()
    turn = await s.handle_user(said)
    assert [x.type for x in turn.actions] == [action]


async def test_kip_kapaliyken_llm_anlama_cagrilmaz():
    llm = MapLlm({})
    a = DialogAgent(llm, ToolRegistry(_index()), DialogConfig(llm_enabled=False, understand="llm"))
    s = a.new_session("u5")
    s.greeting()
    await s.handle_user("muhasebeye bağlar mısınız")
    assert llm.calls == 0


async def test_birimde_unvan_dogrudan_bulunur():
    # ölçüm 3, F05: kalıp "müşteri hizmetleri müdür" kural yolunda Genel Müdürlük'le karışıyordu
    said = "müşteri temsilcisi değil, müşteri hizmetlerinin müdürüyle görüşmek istiyorum"
    a, _ = _agent({said: {"niyet": "aktar", "unvan": "müdür", "birim": "müşteri hizmetleri"}})
    s = a.new_session("u6")
    s.greeting()
    turn = await s.handle_user(said)
    assert [x.extension for x in turn.actions if x.type == "transfer"] == ["2301"]


def test_birim_es_anlamlisiyla_soylenmis_sayilir():
    # ölçüm 3, F08: "personelin müdürü" → LLM "insan kaynakları" yazdı, uydurma sayılmamalı
    a, _ = _agent({})
    said = "personelin müdürü yusuf bey miydi, ona bağlanabilir miyim"
    got = Understanding("aktar", ad="yusuf", hitap="bey", unvan="müdür", birim="insan kaynakları")
    assert got.problem(said, a._vocabulary()[1], a._unit_said(said)) is None
    assert got.problem(said, a._vocabulary()[1]) is not None


async def _turn(said, answer):
    a, _ = _agent({said: answer})
    s = a.new_session("u7")
    s.greeting()
    return await s.handle_user(said)


async def test_unvan_es_anlamlisi_birimde_bulunur():
    # ölçüm 4, F02: LLM "sekreter" yazdı, kalıp yolu Genel Müdürlük hattına aktarmıştı
    turn = await _turn("genel müdürün sekreterine bağlar mısınız acaba rica etsem",
                       {"niyet": "aktar", "unvan": "sekreter", "birim": "Genel Müdürlük"})
    assert [x.extension for x in turn.actions if x.type == "transfer"] == ["1104"]


async def test_sozle_onarim_soyad_eklenir_uydurma_birim_atilir():
    # ölçüm 4, F04: LLM soyadı yazmadı, uydurma birim ve unvan ekledi
    said = "geçen hafta selin hanımla konuşmuştum, soyadı ekinci miydi neydi, ona bağlayın"
    turn = await _turn(said, {"niyet": "aktar", "ad": "selin", "hitap": "hanım", "unvan": "asistan",
                              "birim": "İnsan Kaynakları"})
    assert [x.extension for x in turn.actions if x.type == "transfer"] == ["1401"]


async def test_yalniz_birim_dogrudan_hatta():
    # ölçüm 4, F15: kalıp "yurt dışı satış" kural yolunda bulunamamıştı
    turn = await _turn("yurt dışına satış yapan bölümünüz hangisiyse oraya bağlayın",
                       {"niyet": "aktar", "birim": "Yurt Dışı Satış"})
    assert [x.extension for x in turn.actions if x.type == "transfer"] == ["1240"]


async def test_hedefsiz_aktar_tahmini_birimle_sorulur():
    # ölçüm 4, F09: niyet aktar, hedef yok, tahmini birim var
    turn = await _turn("ekranım kararıyor yardım edecek birini arıyorum",
                       {"niyet": "aktar", "konu": "ekran", "tahmini_birim": "Teknik Destek"})
    assert not turn.actions and "Teknik Destek" in turn.text


def test_sozde_olmayan_unvan_atilir_tarifle_kalir():
    names = frozenset({"zafer"})

    def unit_said(_):
        return True

    made_up = Understanding("aktar", unvan="müdür", birim="trafik")       # ölçüm 5, D20: uydurma unvan
    assert made_up.repaired("trafikten zafer geçmiş ile görüşecektim", names, frozenset(), unit_said)[0].unvan == ""
    described = Understanding("aktar", unvan="müdür", birim="muhasebe")   # tarif sözcüğü: unvan kalır
    assert described.repaired("muhasebenin başındaki kişi", names, frozenset(), unit_said)[0].unvan == "müdür"
    synonym = Understanding("aktar", unvan="asistan", birim="genel müdürlük")
    assert synonym.repaired("genel müdürün sekreterine", names, frozenset(), unit_said)[0].unvan == "asistan"


async def test_kural_turu_bulursa_llm_cagrilmaz_bulamazsa_iz_birakmaz():
    a, llm = _agent({})
    s = a.new_session("u8")
    s.greeting()
    before = s.state.failed_attempts
    assert a._rule_if_found(s, "zzz qqq") is None
    assert s.state.failed_attempts == before
    found = a._rule_if_found(s, "satış müdürü")
    assert found is not None and llm.calls == 0


async def test_konu_tahmini_kurallarin_arkasinda_bekler():
    # ölçüm 6, D57: sözde açıkça geçen birim ("muhasebeyle") LLM'in konu tahmininden önce gelir
    said = "geçen hafta bir sipariş verdim faturası hala gelmedi muhasebeyle görüşmem lazım"
    turn = await _turn(said, {"niyet": "konu", "konu": "sipariş", "tahmini_birim": "Müşteri Hizmetleri"})
    assert [x.extension for x in turn.actions if x.type == "transfer"] == ["1400"]


async def test_hedefli_selam_aktar_sayilir():
    # ölçüm 6, D16: "ali veli lütfen" → LLM selam + ad/soyad dedi, karşılama yinelenmişti
    turn = await _turn("kolay gelsin selin ekinci hanımı rica edecektim",
                       {"niyet": "selam", "ad": "selin", "soyad": "ekinci"})
    assert [x.extension for x in turn.actions if x.type == "transfer"] == ["1401"]
