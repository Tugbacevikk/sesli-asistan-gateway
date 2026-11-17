"""Hızlı yol (ADR-016) birim hattına atlamadan önce sözde daha özel bir hat açıkça söylenmiş mi bakar: "jandarmanın
nizamiyesine" Komutanlığın genel hattına değil Nizamiye'ye, "jandarma personele" Personel Şube'ye (şive + jandarma
seti J03, J07: önceden ikisi de Komutanlık hattına yanlış aktarılıyordu)."""
import asyncio
import json
from pathlib import Path

import pytest

from sesli_asistan.gateway.core.dialog import DialogAgent, DialogConfig
from sesli_asistan.gateway.core.models import Contact
from sesli_asistan.gateway.core.search import DirectoryIndex
from sesli_asistan.gateway.core.tools import ToolRegistry

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def agent() -> DialogAgent:
    data = json.loads((ROOT / "data/demo_rehber.json").read_text(encoding="utf-8"))
    index = DirectoryIndex(Contact.from_api(c) for c in data["contacts"])
    return DialogAgent(None, ToolRegistry(index), DialogConfig(llm_enabled=False))


def _aktarilan(agent: DialogAgent, soz: str) -> list[str]:
    s = agent.new_session("t")
    s.greeting()
    turn = asyncio.run(s.handle_user(soz))
    return [a.extension for a in turn.actions if a.type == "transfer"]


@pytest.mark.parametrize(("soz", "dahili"), [
    ("jandarma nizamiye", "30081"),
    ("jandarmanın nizamiyesine verusun beni", "30081"),
    ("jandarmanın nöbetçi amirine bağlayın", "30080"),
    ("efem jandarma personele bağlayıver hele", "30200"),
])
def test_ozel_hat_birim_hattina_yeglenir(agent: DialogAgent, soz: str, dahili: str) -> None:
    assert _aktarilan(agent, soz) == [dahili]


@pytest.mark.parametrize("soz", ["jandarmaya bağlayın", "il jandarma komutanına bağlar mısınız"])
def test_genel_hat_degismedi(agent: DialogAgent, soz: str) -> None:
    """Yalnız kurum adı (ya da aktarılamaz makam, ADR-025) söylenince yine birim hattı."""
    assert _aktarilan(agent, soz) == ["30000"]
