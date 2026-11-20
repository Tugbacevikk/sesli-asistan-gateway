"""Teyit sorusuna anlaşılmayan cevap: soru bir kez yinelenir; önerilen kişinin adı yinelenirse evet sayılır.
Sesli set 20.11.2026 (arkada ikinci konuşan): "he" → "geçen ay aş" duyuldu, asistan "bulamadım" deyip soruyu
unutuyordu."""
from __future__ import annotations

import asyncio

from sesli_asistan.gateway.composition import build_agent, build_directory
from sesli_asistan.gateway.config import GatewaySettings

ILK = "uşağum muhasebeden selin hanumla görüşeceğum"     # → "Selin Ekinci'ye mi bağlamamı istersiniz?"


def _konus(*sozler: str) -> list[tuple[str, list[str]]]:
    async def run() -> list[tuple[str, list[str]]]:
        s = GatewaySettings(llm_enabled=False, understand_mode="kural", directory_path="data/sirket_rehber.json")
        d = build_directory(s)
        await d.refresh()
        ses = build_agent(s, d).new_session("t")
        ses.greeting()
        out = []
        for t in sozler:
            tr = await ses.handle_user(t)
            out.append((tr.text, [a.extension for a in tr.actions if a.type == "transfer"]))
        return out
    return asyncio.run(run())


def test_anlasilmayan_cevapta_soru_yinelenir_sonra_evet_aktarir() -> None:
    teyit, yine, evet = _konus(ILK, "geçen ay aş", "he")
    assert "Selin Ekinci" in teyit[0] and not teyit[1]
    assert yine[0].startswith("Anlayamadım") and "Selin Ekinci" in yine[0] and not yine[1]
    assert evet[1] == ["1401"]


def test_ikinci_anlasilmazlikta_birakilir_aktarilmaz() -> None:
    _, _, ikinci = _konus(ILK, "geçen ay aş", "efendim canım")
    assert not ikinci[1] and not ikinci[0].startswith("Anlayamadım")


def test_hayir_yine_hayir() -> None:
    _, hayir = _konus(ILK, "hayır")
    assert not hayir[1] and not hayir[0].startswith("Anlayamadım")


def test_onerilen_kisinin_adi_yinelenince_evet_sayilir() -> None:
    _, ad = _konus(ILK, "selin abi")
    assert ad[1] == ["1401"]
