"""Küfür / hakaret: ilkinde uyarı, tekrarında görüşme kapatılır (kural kipi; LLM'e bağlı değil)."""
from __future__ import annotations

import asyncio

import pytest

from sesli_asistan.gateway.composition import build_agent, build_directory
from sesli_asistan.gateway.config import GatewaySettings
from sesli_asistan.gateway.core.phrases import is_abusive


@pytest.mark.parametrize("soz", ["siktir git", "lan salaksınız", "amk operatör", "aptal makine", "orospunun çocuğu"])
def test_kufur_taninir(soz: str) -> None:
    assert is_abusive(soz)


@pytest.mark.parametrize("soz", [
    "malın bir kısmı bozuk geldi kaliteye vereceğiz", "muhasebeye bağlayın", "ali bey ile görüşmek istiyorum",
    "bilgisayarım açılmıyor", "yurt dışı bm ile görüşmek istiyorum", "satış müdürü kim", "bokluca mahallesi",
])
def test_normal_soz_kufur_sayilmaz(soz: str) -> None:
    assert not is_abusive(soz)


def _konus(*sozler: str) -> list[tuple[str, list[str], list[str]]]:
    async def run() -> list[tuple[str, list[str], list[str]]]:
        s = GatewaySettings(llm_enabled=False, understand_mode="kural", directory_path="data/sirket_rehber.json")
        d = build_directory(s)
        await d.refresh()
        ses = build_agent(s, d).new_session("t")
        ses.greeting()
        out = []
        for t in sozler:
            tr = await ses.handle_user(t)
            out.append((tr.text, [a.extension for a in tr.actions if a.type == "transfer"],
                        [a.type for a in tr.actions if a.type == "hangup"]))
        return out
    return asyncio.run(run())


def test_ilk_kufurde_uyarir_kapatmaz() -> None:
    (metin, aktarma, kapat), = _konus("siktir git be")
    assert "saygılı" in metin and not aktarma and not kapat


def test_kufur_ile_gecerli_istek_ayni_sozde_aktarilmaz() -> None:
    (_, aktarma, kapat), = _konus("amk muhasebeyi bağla")
    assert not aktarma and not kapat


def test_israrda_kapatir() -> None:
    uyari, kapatma = _konus("lan aptal", "salak mısın")
    assert not uyari[2] and kapatma[2] == ["hangup"]


def test_uyarıdan_sonra_normal_istek_calisir() -> None:
    uyari, normal = _konus("lan aptal", "muhasebeye bağlayın")
    assert normal[1] == ["1400"] and not normal[2]
