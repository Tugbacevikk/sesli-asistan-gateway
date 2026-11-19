"""F25: alt birim tam uyunca üst birim yakın aday sayılmaz."""
from __future__ import annotations

import asyncio

from sesli_asistan.gateway.composition import build_agent, build_directory
from sesli_asistan.gateway.config import GatewaySettings


def test_stt_bm_bozulmasi_alt_birime_aktarilir() -> None:
    # STT "bölüm"ü "bm" yazdı; "Yurt Dışı Satış" (100) ile üst birim "Satış" (95) yakın sayılıp "bulamadım" deniyordu
    async def run() -> list[str]:
        s = GatewaySettings(llm_enabled=False, understand_mode="kural", directory_path="data/sirket_rehber.json")
        d = build_directory(s)
        await d.refresh()
        ses = build_agent(s, d).new_session("t")
        ses.greeting()
        tr = await ses.handle_user("yurt dışı bm ile görüşmek istiyorum")
        return [a.extension for a in tr.actions if a.type == "transfer"]
    assert asyncio.run(run()) == ["1240"]


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


def test_ust_birim_soylenince_alt_birimler_sorulur_bulamadim_denmez() -> None:
    soru, secim = _konus("satışa bağlayın", "kurumsal")
    assert "Kurumsal Satış" in soru[0] and "Yurt İçi Satış" in soru[0] and soru[1] == []
    assert secim[1] == ["1260"]


def test_arkadan_duyulan_tek_birim_sozu_dogrudan_aktarmaz() -> None:
    # sesli set 19.11.2026: "personel" arkadan duyuldu, sözün kalanı başka bir soru → teyit, aktarma yok
    async def run() -> tuple[str, list[str]]:
        s = GatewaySettings(llm_enabled=False, understand_mode="kural", directory_path="data/rehber_ornek.json")
        d = build_directory(s)
        await d.refresh()
        ses = build_agent(s, d).new_session("t")
        ses.greeting()
        tr = await ses.handle_user("personel akşam kaça kadar telefonlara bakıyorsunuz")
        return tr.text, [a.extension for a in tr.actions if a.type == "transfer"]
    metin, aktarma = asyncio.run(run())
    assert not aktarma and "bağlamamı istersiniz" in metin
