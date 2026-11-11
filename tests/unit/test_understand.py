"""ADR-037 "LLM önce anlar" kipi: sahte LLM'le (yanıt söze göre). Gerçek modelle karşılaştırma:
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


async def test_dolayli_tarif_unvan_ve_birime_cevrilir():
    a, llm = _agent({"muhasebenin başındaki kişi kimse onunla": {"niyet": "aktar", "unvan": "müdür",
                                                                  "birim": "muhasebe"}})
    s = a.new_session("u1")
    s.greeting()
    turn = await s.handle_user("muhasebenin başındaki kişi kimse onunla")
    assert [x.extension for x in turn.actions if x.type == "transfer"] == ["1401"]
    assert llm.calls == 1                       # anlama turu; ikinci (araçlı) LLM turu yok


async def test_konudan_birim_teyitle_sorulur():
    a, _ = _agent({"bilgisayarım açılmıyor": {"niyet": "konu", "konu": "arıza", "tahmini_birim": "Teknik Destek"},
                   "evet": {"niyet": "evet"}})
    s = a.new_session("u2")
    s.greeting()
    asked = await s.handle_user("bilgisayarım açılmıyor")
    assert not asked.actions and "Teknik Destek" in asked.text
    done = await s.handle_user("evet")
    assert [x.extension for x in done.actions if x.type == "transfer"] == ["1760"]


async def test_llm_dusunce_kural_kipi_surer():
    a, llm = _agent({})                         # her çağrıda hata
    s = a.new_session("u3")
    s.greeting()
    turn = await s.handle_user("muhasebeye bağlar mısınız")
    assert [x.extension for x in turn.actions if x.type == "transfer"] == ["1400"]
    assert llm.calls >= 1


@pytest.mark.parametrize("said, niyet, action", [
    ("ya ben en iyisi operatörle konuşayım", "operator", "operator"),
    ("tamam gerek kalmadı kapatabilirsiniz", "kapat", "hangup"),
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
