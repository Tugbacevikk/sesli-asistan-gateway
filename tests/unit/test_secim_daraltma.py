"""ADR-039: kural birden çok aday bulup soracakken LLM'e de sorulur. LLM adaylardan tam birini gösterirse teyit
(ürün varsayılanı) ya da doğrudan aktarma; göstermezse kuralın sorusu. LLM rehberde olmayan birini seçemez, sözde
geçmeyen birimi yazarsa reddedilir. Örnekler şive setinden (S03, S07): gemma-santral v3'ün gerçek çıktıları."""
import asyncio
import json
from pathlib import Path

import pytest

from sesli_asistan.gateway.core.dialog import DialogAgent, DialogConfig
from sesli_asistan.gateway.core.models import Contact
from sesli_asistan.gateway.core.ports import LlmMessage
from sesli_asistan.gateway.core.search import DirectoryIndex
from sesli_asistan.gateway.core.tools import ToolRegistry

ROOT = Path(__file__).resolve().parents[2]
CEVAP = {
    "hukukçi ali beye vereceksun beni da": '{"niyet":"aktar","ad":"ali","hitap":"bey","birim":"hukuk"}',
    "ben üretimden elif hanımla görüşcem": '{"niyet":"aktar","ad":"elif","hitap":"hanım","birim":"üretim"}',
    "lojistikçi ali beye vereceksun beni da": '{"niyet":"aktar","ad":"ali","hitap":"bey","birim":"lojistik"}',
    "pazarlamacı ali beye vereceksun beni da": '{"niyet":"aktar","ad":"ali","hitap":"bey","birim":"hukuk"}',
    "hukukçi ali beye vereceksun beni": '{"niyet":"belirsiz"}',
}


class Llm:
    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages: list[dict], tools: list[dict]) -> LlmMessage:
        self.calls += 1
        return LlmMessage(CEVAP.get(messages[-1]["content"], '{"niyet":"belirsiz"}'))


@pytest.fixture(scope="module")
def index() -> DirectoryIndex:
    data = json.loads((ROOT / "data/sirket_rehber.json").read_text(encoding="utf-8"))
    return DirectoryIndex(Contact.from_api(c) for c in data["contacts"])


def _konus(index: DirectoryIndex, kip: str, *sozler: str):
    llm = Llm()
    s = DialogAgent(llm, ToolRegistry(index), DialogConfig(understand="llm", understand_narrow=kip)).new_session("t")
    s.greeting()
    turns = [asyncio.run(s.handle_user(x)) for x in sozler]
    return turns, s, llm


def _aktarilan(turn) -> list[str]:
    return [a.extension for a in turn.actions if a.type == "transfer"]


@pytest.mark.parametrize(("soz", "dahili"), [("hukukçi ali beye vereceksun beni da", "2401"),
                                              ("ben üretimden elif hanımla görüşcem", "1801")])
def test_teyit_sonra_aktarma(index: DirectoryIndex, soz: str, dahili: str) -> None:
    (ilk, ikinci), s, llm = _konus(index, "teyit", soz, "evet")
    assert not ilk.actions and "bağlamamı istersiniz" in ilk.text and llm.calls == 1
    assert _aktarilan(ikinci) == [dahili]


def test_teyitsiz_aktarma_kipi_yok() -> None:
    from sesli_asistan.gateway.config import GatewaySettings
    with pytest.raises(ValueError):
        GatewaySettings(understand_narrow="aktar")


def test_kapali_kipte_kural_sorusu_ve_llm_yok(index: DirectoryIndex) -> None:
    (turn,), s, llm = _konus(index, "kapali", "hukukçi ali beye vereceksun beni da")
    assert not turn.actions and s.state.choice and llm.calls == 0


@pytest.mark.parametrize("soz", [
    "lojistikçi ali beye vereceksun beni da",     # LLM'in birimi adaylardan hiçbirine uymuyor
    "pazarlamacı ali beye vereceksun beni da",    # LLM sözde geçmeyen birimi (hukuk) yazdı: reddedilir
    "hukukçi ali beye vereceksun beni",           # LLM anlamadı
])
def test_daraltilamazsa_kural_sorusu(index: DirectoryIndex, soz: str) -> None:
    (turn,), s, _ = _konus(index, "teyit", soz)
    assert not turn.actions and not s.state.confirm and s.state.choice


# --- şive + jandarma seti (demo rehberi): soru daha önceki adımlarda sorulur (çakışma adımı, kişi seçimi) ---
CEVAP.update({
    "jandarmanın asayiş şube müdürünü istirem": '{"niyet":"aktar","unvan":"şube müdürü","birim":"jandarma asayiş"}',
    "asayişteki uğur astsubayla görüşcem": '{"niyet":"aktar","ad":"uğur","unvan":"astsubay","birim":"asayiş"}',
    "asayiş şube müdürünü istirem": '{"niyet":"aktar","unvan":"asayiş şube müdürü"}',
})


@pytest.fixture(scope="module")
def demo() -> DirectoryIndex:
    data = json.loads((ROOT / "data/demo_rehber.json").read_text(encoding="utf-8"))
    return DirectoryIndex(Contact.from_api(c) for c in data["contacts"])


def test_kurum_ve_sube_soylenince_kural_dogrudan_bulur(demo: DirectoryIndex) -> None:
    # J06: "jandarma asayiş" birimin tam eş adı, "şube müdürü" o birimde tek: LLM'e gerek kalmadan aktarılır
    # (25.11.2026'ya kadar kural iki şubeyi soruyor, LLM daraltıp teyit soruyordu)
    (turn,), _, _ = _konus(demo, "teyit", "jandarmanın asayiş şube müdürünü istirem")
    assert _aktarilan(turn) == ["30101"]


@pytest.mark.parametrize(("soz", "dahili"), [
    ("asayişteki uğur astsubayla görüşcem", "30131"),        # J09: üç şubede Uğur astsubay; "asayişteki"
])
def test_onceki_adimin_sorusu_da_daraltilir(demo: DirectoryIndex, soz: str, dahili: str) -> None:
    (ilk, ikinci), _, _ = _konus(demo, "teyit", soz, "he")
    assert not ilk.actions and "bağlamamı istersiniz" in ilk.text
    assert _aktarilan(ikinci) == [dahili]


def test_kurum_soylenmeyince_kurum_sorusu(demo: DirectoryIndex) -> None:
    (turn,), s, _ = _konus(demo, "teyit", "asayiş şube müdürünü istirem")
    assert not turn.actions and not s.state.confirm and "Jandarma" in turn.text and "Emniyet" in turn.text
