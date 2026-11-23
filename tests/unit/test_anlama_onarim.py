"""LLM çıktısının sözle onarımı: arayanın kendi adı (D43) ve atlanan birim (D20). v7 ölçümü 23.11.2026."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from sesli_asistan.gateway.core.dialog import DialogAgent, DialogConfig
from sesli_asistan.gateway.core.dialog.understand import Understanding
from sesli_asistan.gateway.core.models import Contact
from sesli_asistan.gateway.core.ports import LlmError, LlmMessage
from sesli_asistan.gateway.core.search import DirectoryIndex
from sesli_asistan.gateway.core.tools import ToolRegistry

ROOT = Path(__file__).resolve().parents[2]


class MapLlm:
    def __init__(self, answers: dict[str, dict]):
        self.answers = answers

    async def chat(self, messages, tools):
        said = messages[-1]["content"]
        if said not in self.answers:
            raise LlmError("yok")
        return LlmMessage(json.dumps(self.answers[said], ensure_ascii=False))


def _konus(rehber: str, soz: str, cikti: dict) -> list[str]:
    data = json.loads((ROOT / rehber).read_text(encoding="utf-8"))
    index = DirectoryIndex(Contact.from_api(c) for c in data["contacts"])
    agent = DialogAgent(MapLlm({soz: cikti}), ToolRegistry(index), DialogConfig(understand="llm"))

    async def run() -> list[str]:
        ses = agent.new_session("t")
        ses.greeting()
        tr = await ses.handle_user(soz)
        return [a.extension for a in tr.actions if a.type == "transfer"]
    return asyncio.run(run())


def test_arayanin_kendi_adi_aranan_sanilmaz() -> None:
    # v7: "adım elif ... burak beyi" → ad elif, soyad burak (birim yok)
    soz = "adım elif muhasebeden burak beyi arıyorum"
    assert _konus("data/rehber_ornek.json", soz, {"niyet": "aktar", "ad": "elif", "soyad": "burak"}) == ["215"]


def test_sozde_gecen_birim_eklenir_silinmis_kiside_birim_hatti() -> None:
    # v7: "trafikten zafer geçmiş" → yalnız ad soyad; Zafer Geçmiş silinmiş, beklenen Trafik Şube hattı
    soz = "trafikten zafer geçmiş ile görüşecektim"
    assert _konus("data/demo_rehber.json", soz, {"niyet": "aktar", "ad": "zafer", "soyad": "geçmiş"}) == ["40200"]


def test_kendini_tanitma_yoksa_ad_korunur() -> None:
    got, notes = Understanding(niyet="aktar", ad="elif").repaired(
        "elif hanımla görüşecektim", frozenset({"elif"}), frozenset(), lambda b: False)
    assert got.ad == "elif" and not any("kendi adı" in n for n in notes)
