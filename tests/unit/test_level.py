"""Söze girmede ses seviyesi kapısı (core/level.py)."""
import numpy as np

from sesli_asistan.gateway.core.level import LevelGate, frame_db
from sesli_asistan.libs.audio import g711


def tone(db, n=160):
    amp = 10 ** (db / 20) * np.sqrt(2)
    return (amp * np.sin(2 * np.pi * 300 * np.arange(n) / 8000)).astype(np.float32)


def feed(g, t0, db, seconds):
    t = t0
    for _ in range(int(seconds / 0.02)):
        g.add(t, db)
        t += 0.02
    return t


def test_kare_seviyesi_g711_ve_pcm16():
    assert abs(frame_db(g711.encode(tone(-30), "pcma"), "pcma") + 30) < 1.5
    pcm = (tone(-20, 320) * 32767).astype("<i2").tobytes()
    assert abs(frame_db(pcm, "g722") + 20) < 1.0
    assert frame_db(b"", "g722") == -120.0


def test_arayan_seviyesi_ogrenilir_arka_plan_soze_giremez():
    g = LevelGate(margin_db=6)
    t = feed(g, 0.0, -30, 1.5)                    # arayan konuştu (anlaşılan söz)
    g.learn(0.0, t)
    assert abs(g.caller_db + 30) < 0.1
    t2 = feed(g, t, -38, 0.5)                     # arkadaki video (canlı ölçüm: -37 / -38 dBFS)
    assert not g.allows_barge_in(t2)
    t3 = feed(g, t2, -29, 0.4)                    # arayan yeniden konuşuyor
    assert g.allows_barge_in(t3)


def test_seviye_bilinmiyorken_yalniz_cok_kisik_sesler_elenir():
    g = LevelGate(margin_db=6, floor_db=-45)
    assert g.allows_barge_in(feed(g, 0.0, -38, 0.4))
    g2 = LevelGate(margin_db=6, floor_db=-45)
    assert not g2.allows_barge_in(feed(g2, 0.0, -48, 0.4))


def test_sessiz_kareler_pencereye_girmez_ve_kapi_kapatilabilir():
    g = LevelGate(margin_db=6)
    g.learn(0.0, feed(g, 0.0, -30, 1.0))
    t = feed(g, 1.0, -29, 0.1)
    t = feed(g, t, -120, 0.3)                     # paketler toplu geldi: sözden sonraki sessizlik de pencerede
    assert g.allows_barge_in(t)
    off = LevelGate(margin_db=0)
    assert off.allows_barge_in(feed(off, 0.0, -60, 0.4))
