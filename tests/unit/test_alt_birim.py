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
