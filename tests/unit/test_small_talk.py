"""Kısa sohbet (ADR-035): araçsız LLM çağrısı, cümle denetimi, kod cevabına düşme."""
from __future__ import annotations

import asyncio

import pytest

from sesli_asistan.gateway.core.dialog import DialogAgent, DialogConfig
from sesli_asistan.gateway.core.dialog.small_talk import SMALL_TALK_PROMPT, clean_small_talk
from sesli_asistan.gateway.core.models import Contact
from sesli_asistan.gateway.core.ports import LlmError, LlmMessage
from sesli_asistan.gateway.core.search import DirectoryIndex
from sesli_asistan.gateway.core.tools import ToolRegistry

REHBER = [Contact.from_api({"id": "1", "first_name": "Ayşe", "last_name": "Yılmaz", "extension": "1001",
                            "department": "Muhasebe"})]


class _Llm:
    """Araç desteklemeyen model gibi: araçlı çağrı hata, araçsız çağrı verilen cevabı döner."""

    def __init__(self, cevap: str = "Günaydın, iyiyim, teşekkür ederim.", hata: bool = False):
        self.cevap, self.hata, self.calls = cevap, hata, []

    async def chat(self, messages, tools):
        self.calls.append((messages, tools))
        if tools or self.hata:
            raise LlmError("does not support tools")
        return LlmMessage(self.cevap)


def _turn(llm, text="günaydın nasılsınız", small_talk=True):
    a = DialogAgent(llm, ToolRegistry(DirectoryIndex(REHBER)),
                    DialogConfig(organization="D", small_talk=small_talk, stream_replies=False, understand="llm"))
    return a, asyncio.run(a.new_session("c").handle_user(text))


def test_arac_desteklemeyen_modelle_sicak_cevap_ve_soru():
    llm = _Llm()
    a, turn = _turn(llm)
    assert turn.text == f"Günaydın, iyiyim, teşekkür ederim. {a.cfg.texts.ask_target}"
    assert not turn.actions
    messages, tools = llm.calls[-1]
    assert tools == [] and messages[0]["content"] == SMALL_TALK_PROMPT


@pytest.mark.parametrize("cevap", [
    "", '{"niyet":"selam"}', "Muhasebeye aktarıyorum.", "Siz nasılsınız?", "Dahili 1001'i arayabilirsiniz.",
    "Günaydın " * 30,
])
def test_denetimden_gecmeyen_cevapta_kod_selami(cevap):
    a, turn = _turn(_Llm(cevap))
    assert turn.text.startswith("Günaydın,") and not turn.actions
    assert "aktar" not in turn.text.lower()


def test_llm_duserse_kod_selami():
    a, turn = _turn(_Llm(hata=True))
    assert turn.text.startswith("Günaydın,") and not turn.actions


@pytest.mark.parametrize("raw, beklenen", [
    ("Günaydın, iyiyim, teşekkür ederim", "Günaydın, iyiyim, teşekkür ederim."),
    ("  “sağ olun, size de kolay gelsin!”  ", "Sağ olun, size de kolay gelsin!"),
    ("İyiyim, teşekkürler. Size nasıl yardımcı olabilirim?", "İyiyim, teşekkürler."),
    ("Tamam", None),
])
def test_clean_small_talk(raw, beklenen):
    assert clean_small_talk(raw) == beklenen


def test_turkce_buyuk_harf():
    assert clean_small_talk("iyiyim, teşekkür ederim") == "İyiyim, teşekkür ederim."
