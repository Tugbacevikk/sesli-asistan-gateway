"""Birim + kişi adı (hitap STT'de kaybolmuş): birim hattına / birimin tek kişisine gidilmez, "kiminle?" sorulur.
Sesli set 20.11.2026, seviye kapısı 9 dB: "murat abi" → "murata ve", "selin abla" → "selin hava" duyuldu."""
from __future__ import annotations

import asyncio

import pytest

from sesli_asistan.gateway.composition import build_agent, build_directory
from sesli_asistan.gateway.config import GatewaySettings
from sesli_asistan.gateway.core.people.names import says_first_name


def _ilk(soz: str) -> tuple[str, list[str]]:
    async def run() -> tuple[str, list[str]]:
        s = GatewaySettings(llm_enabled=False, understand_mode="kural", directory_path="data/rehber_ornek.json")
        d = build_directory(s)
        await d.refresh()
        ses = build_agent(s, d).new_session("t")
        ses.greeting()
        tr = await ses.handle_user(soz)
        return tr.text, [a.extension for a in tr.actions if a.type == "transfer"]
    return asyncio.run(run())


@pytest.mark.parametrize("soz", ["murata ve vardiya insan kaynaklarında ona bir bağla",
                                 "selin hava ile görüşecektim muhasebeden"])
def test_birimle_ad_soylenince_sorulur(soz: str) -> None:
    metin, aktarma = _ilk(soz)
    assert not aktarma and "kiminle görüşmek istersiniz" in metin


@pytest.mark.parametrize("soz,dahili", [("insan kaynaklarına bağlayın", "260"), ("muhasebeye bağlayın", "210")])
def test_yalniz_birim_eskisi_gibi(soz: str, dahili: str) -> None:
    assert _ilk(soz)[1] == [dahili]


@pytest.mark.parametrize("soz", ["deniz kenarındaki şube", "bayram tatilinde açık mısınız", "nilüfer kaymakamı",
                                 "faturam yanlış geldi", "yıldız sokak"])
def test_gunluk_sozcuk_ad_sayilmaz(soz: str) -> None:
    assert not says_first_name(soz)
