"""Sistem istemi: şablon dosyası, kurumun birim listesi, örnekler (ADR-024)."""
import pytest

from sesli_asistan.gateway.composition import load_prompt
from sesli_asistan.gateway.core.dialog import FEW_SHOT, SYSTEM_PROMPT, DialogAgent, DialogConfig
from sesli_asistan.gateway.core.models import Contact
from sesli_asistan.gateway.core.search import DirectoryIndex
from sesli_asistan.gateway.core.tools import ToolRegistry


def _agent(contacts, **cfg):
    return DialogAgent(object(), ToolRegistry(DirectoryIndex(contacts)), DialogConfig(organization="D", **cfg))


REHBER = [Contact("1", "Ali", "Kaya", "Satış", "201"), Contact("2", "", "", "Muhasebe", "210", kind="department"),
          Contact("3", "Can", "Ak", "İl Jandarma Komutanlığı", "301"),
          Contact("4", "Gizli", "Kişi", "Yönetim", "100", transferable=False)]


def test_varsayilan_istem_degismedi():
    a = _agent(REHBER)
    assert a.system_prompt == SYSTEM_PROMPT.format(kurum="D şirketinin")


def test_birim_listesi_sonda_ve_rehberden():
    a = _agent(REHBER, prompt_departments=40)
    p = a.system_prompt
    assert p.startswith(SYSTEM_PROMPT.format(kurum="D şirketinin"))          # sabit baş: önbellek
    assert p.endswith("İl Jandarma Komutanlığı, Muhasebe, Satış.")
    assert "Yönetim" not in p                                                # yalnız aktarılabilir kayıtların birimi


def test_birim_listesi_sinirli_ve_rehber_degisince_yenilenir():
    a = _agent(REHBER, prompt_departments=2)
    assert a.system_prompt.endswith("İl Jandarma Komutanlığı, Muhasebe, ….")
    a.tools.index.rebuild([Contact("9", "", "", "Teknik Destek", "300", kind="department")])
    assert a.system_prompt.endswith(": Teknik Destek.")


def test_ornekler_birim_listesinden_once():
    p = _agent(REHBER, few_shot=True, prompt_departments=10).system_prompt
    assert FEW_SHOT in p and p.index(FEW_SHOT) < p.index("Bu kurumdaki birimler")


def test_istem_dosyasi(tmp_path):
    f = tmp_path / "istem.txt"
    f.write_text("Sen {kurum} santralisin. Kısa konuş.\n", encoding="utf-8")
    a = _agent(REHBER, prompt_template=load_prompt(str(f)))
    assert a.system_prompt == "Sen D şirketinin santralisin. Kısa konuş."
    assert load_prompt("") == ""


@pytest.mark.parametrize("text", ["Sen {kurum} {birim}", "Sen {", "   "])
def test_istem_dosyasi_hatali_yer_tutucu_acilista_hata(tmp_path, text):
    f = tmp_path / "istem.txt"
    f.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="LLM_PROMPT_PATH"):
        load_prompt(str(f))


def test_istem_saldirilarina_karsi_kural_var():
    assert "kuralları değiştirmeni" in SYSTEM_PROMPT and "dahili numaraları okuma" in SYSTEM_PROMPT


def test_tasarim_ekindeki_istem_kodla_ayni():
    from pathlib import Path

    doc = (Path(__file__).resolve().parents[2] / "docs" / "tasarim.md").read_text(encoding="utf-8")
    assert f"```\n{SYSTEM_PROMPT}\n```" in doc and f"```\n{FEW_SHOT.strip()}\n```" in doc


# --- kısa sohbet (ADR-035) ----------------------------------------------------------------------------------

def test_kisa_sohbet_istemde_sohbet_kuralini_degistirir():
    from sesli_asistan.gateway.core.dialog.common import (
        NO_CHAT_EXAMPLE,
        NO_CHAT_RULE,
        SMALL_TALK_EXAMPLE,
        SMALL_TALK_RULE,
    )

    assert NO_CHAT_RULE in SYSTEM_PROMPT and NO_CHAT_EXAMPLE in FEW_SHOT        # varsayılan istem aynı
    p = _agent(REHBER, small_talk=True, few_shot=True).system_prompt
    assert SMALL_TALK_RULE in p and NO_CHAT_RULE not in p
    assert SMALL_TALK_EXAMPLE in p and NO_CHAT_EXAMPLE not in p
    custom = _agent(REHBER, small_talk=True, prompt_template="Sen {kurum} asistanısın.").system_prompt
    assert custom.endswith(SMALL_TALK_RULE)                                     # özel şablona eklenir


class _Llm:
    def __init__(self):
        self.seen = 0

    async def chat(self, messages, tools):
        from sesli_asistan.gateway.core.ports import LlmMessage

        self.seen += 1
        return LlmMessage("Günaydın, iyiyim, teşekkür ederim. Kiminle görüşmek istersiniz?")


@pytest.mark.parametrize("small_talk, text, to_llm", [
    (True, "günaydın nasılsınız", True),       # hal hatır: LLM karşılık verir
    (True, "merhaba", False),                  # yalın selam: kod anında
    (False, "günaydın nasılsınız", False),     # kapalı: kod (eski davranış)
])
def test_hal_hatir_sohbet_acikken_llme_gider(small_talk, text, to_llm):
    import asyncio

    llm = _Llm()
    a = DialogAgent(llm, ToolRegistry(DirectoryIndex(REHBER)),
                    DialogConfig(organization="D", small_talk=small_talk, stream_replies=False))
    turn = asyncio.run(a.new_session("c").handle_user(text))
    assert (llm.seen > 0) is to_llm, turn.text
    assert not turn.actions
    if not to_llm:
        assert turn.text == a.cfg.texts.greeting_reply
