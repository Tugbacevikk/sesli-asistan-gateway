"""Mesai saatleri ve tatiller (tasarim.md 5.1.3): kural ayrıştırma ve çağrı davranışı."""
import asyncio
from datetime import datetime

import pytest
from test_call import TEXTS, Call, Llm, tool

from sesli_asistan.gateway.config import GatewaySettings
from sesli_asistan.gateway.core.call import CallState
from sesli_asistan.gateway.core.ports import LlmMessage
from sesli_asistan.gateway.core.schedule import AFTER_HOURS, HOLIDAY, OPEN, OfficeHours

HOURS = "Pzt-Cum 08:30-12:30,13:30-18:00; Cumartesi 09:00-13:00"
HOLIDAYS = "01-01, 10-29, 2026-10-28@13:00, 2027-03-19..2027-03-21"


@pytest.mark.parametrize("when,expected", [
    ("2026-10-26 08:29", AFTER_HOURS), ("2026-10-26 08:30", OPEN), ("2026-10-26 12:45", AFTER_HOURS),
    ("2026-10-26 17:59", OPEN), ("2026-10-26 18:00", AFTER_HOURS), ("2026-10-31 10:00", OPEN),
    ("2026-10-31 13:00", AFTER_HOURS), ("2026-11-01 10:00", AFTER_HOURS),           # pazar yazılmadı: kapalı
    ("2026-10-29 10:00", HOLIDAY), ("2027-10-29 10:00", HOLIDAY), ("2027-01-01 10:00", HOLIDAY),
    ("2026-10-28 11:00", OPEN), ("2026-10-28 13:00", HOLIDAY),                      # arife: 13:00'te kapanır
    ("2027-03-19 10:00", HOLIDAY), ("2027-03-21 23:00", HOLIDAY), ("2027-03-22 10:00", OPEN),
])
def test_mesai_ve_tatil(when, expected):
    assert OfficeHours.parse(HOURS, HOLIDAYS).status(datetime.fromisoformat(when)) == expected


def test_kural_yoksa_hep_acik_yalniz_tatil_de_olabilir():
    assert OfficeHours.parse().always_open
    assert OfficeHours.parse().status(datetime(2026, 1, 1, 3, 0)) == OPEN
    only = OfficeHours.parse("", "01-01")
    assert only.status(datetime(2026, 1, 1, 10)) == HOLIDAY and only.status(datetime(2026, 1, 2, 3)) == OPEN


def test_haftayi_saran_aralik_ve_gece_yarisi():
    o = OfficeHours.parse("cmt-pzt 00:00-24:00")
    assert [o.status(datetime(2026, 10, d, 12)) for d in (24, 25, 26, 27)] == [OPEN, OPEN, OPEN, AFTER_HOURS]


@pytest.mark.parametrize("hours,holidays", [
    ("pzt 08:30", ""), ("pazartesi-cuma 8-18", ""), ("xyz 08:00-18:00", ""), ("pzt 18:00-08:00", ""),
    ("pzt 25:00-26:00", ""), ("", "2026-13-01"), ("", "02-30"), ("", "2026-10-30..2026-10-28"), ("", "01-01@1300"),
])
def test_hatali_kural_acilista_reddedilir(hours, holidays):
    with pytest.raises(ValueError, match="OFFICE_HOURS / HOLIDAYS"):
        GatewaySettings(office_hours=hours, holidays=holidays)


def test_bilinmeyen_saat_dilimi_reddedilir():
    with pytest.raises(ValueError, match="TIMEZONE"):
        GatewaySettings(timezone="Mars/Olympus")


# --- çağrı davranışı ------------------------------------------------------------------------------------

async def _closed_call(office, **cfg):
    c = Call(office=office, **cfg)
    task = asyncio.create_task(c.session.run())
    await c.until(lambda: c.pbx.actions)
    stats_holder = c
    c.session.hangup()
    return stats_holder, await asyncio.wait_for(task, 2)


@pytest.mark.parametrize("office,text", [(AFTER_HOURS, TEXTS.closed), (HOLIDAY, TEXTS.holiday)])
async def test_mesai_disi_anons_ve_kapatma(office, text):
    c, stats = await _closed_call(office)
    assert c.pbx.actions[0]["type"] == "hangup"
    assert stats.outcome == "closed" and stats.reason == office
    assert c.llm.seen == []                         # asistan hiç devreye girmedi
    assert c.media.played >= 1


async def test_kapanis_anonsuna_soze_girilemez_ve_tus_islemez():
    c = Call(office=AFTER_HOURS, play_s=0.2)
    task = asyncio.create_task(c.session.run())
    await c.until(lambda: c.session._speaking())
    c.say("alo alo")
    c.session.dtmf("0")
    await c.until(lambda: c.pbx.actions)
    assert [a["type"] for a in c.pbx.actions] == ["hangup"] and c.session.stats.barge_ins == 0
    c.session.hangup()
    stats = await asyncio.wait_for(task, 2)
    assert stats.outcome == "closed" and c.llm.seen == []


async def test_asistan_modunda_aktarma_calisir():
    llm = Llm(tool("InContacts", Name="Ayşe Yılmaz"))
    async with Call(llm, office=AFTER_HOURS, after_hours_mode="assistant") as c:
        c.say("ayşe yılmaz")
        await c.until(lambda: c.pbx.actions)
        assert c.pbx.actions[0] == {**c.pbx.actions[0], "type": "transfer", "extension": "214"}


@pytest.mark.parametrize("trigger", ["dtmf", "llm"])
async def test_asistan_modunda_operator_yerine_bilgi_ve_kapatma(trigger):
    llm = Llm(tool("ConnectOperator"), LlmMessage("Sizi operatöre bağlıyorum."))
    async with Call(llm, office=HOLIDAY, after_hours_mode="assistant") as c:
        if trigger == "dtmf":
            c.session.dtmf("0")
        else:
            c.say("operatör lütfen")
        await c.until(lambda: c.pbx.actions)
        assert [a["type"] for a in c.pbx.actions] == ["hangup"]
        assert c.session.state == CallState.HANDED_OFF
    assert c.stats.outcome == "closed" and c.stats.reason.startswith("operator_yok:")
    assert c.stats.turns[-1].reply == TEXTS.no_operator


async def test_mesai_icinde_davranis_degismez():
    async with Call(office=OPEN) as c:
        c.session.dtmf("0")
        await c.until(lambda: c.pbx.actions)
        assert c.pbx.actions[0]["type"] == "operator"
