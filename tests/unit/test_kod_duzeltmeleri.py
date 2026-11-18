"""Kural modunda küçük düzeltmeler: birim hattı (J04), "personelin" (F08), ad + soyad (F04), "he" (S17)."""
from __future__ import annotations

import asyncio

from sesli_asistan.gateway.composition import build_agent, build_directory
from sesli_asistan.gateway.config import GatewaySettings


def _konus(rehber: str, *sozler: str) -> list[tuple[str, list[str]]]:
    async def run() -> list[tuple[str, list[str]]]:
        s = GatewaySettings(llm_enabled=False, understand_mode="kural", directory_path=rehber)
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


def test_yalniz_birim_soylenince_birim_hattina_aktarilir() -> None:
    assert _konus("data/demo_rehber.json", "jandarma lojistik")[0][1] == ["30300"]


def test_personelin_insan_kaynaklari_sayilir() -> None:
    assert _konus("data/sirket_rehber.json", "personelin müdürü yusuf bey")[0][1] == ["1601"]


def test_ad_ve_soyad_bitisik_degilse_tek_kisi_teyit_edilir() -> None:
    cevap = _konus("data/sirket_rehber.json", "selin hanımla konuşmuştum soyadı ekinci miydi ona bağlayın", "he")
    assert "Selin Ekinci" in cevap[0][0] and cevap[1][1] == ["1401"]


def test_iki_secenekli_soruya_evet_netlestirir() -> None:
    cevap = _konus("data/sirket_rehber.json", "gardaş malın bi kısmı bozuk geldi kaliteye vereceğiz bunu", "he")
    assert cevap[1][0].startswith("Hangisini istersiniz")
