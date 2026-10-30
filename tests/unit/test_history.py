"""Kalıcı çağrı geçmişi (SQLite)."""
from datetime import datetime, timedelta

from sesli_asistan.gateway.adapters.history.sqlite import SqliteCallHistory
from sesli_asistan.gateway.core.call import CallStats, TurnRecord


def stats(started=None, outcome="transferred", misses=(), heard="muhasebeden ayşe hanım"):
    return CallStats(turns=[TurnRecord(heard, "Sizi Ayşe Yılmaz'a aktarıyorum.", ["transfer:214"], 0.4, 1.2)],
                     outcome=outcome, reason="cevaplandi", channel="santral",
                     started=started or datetime.now().isoformat(timespec="seconds"), duration_s=12.5,
                     misses=list(misses))


def test_yeniden_acilinca_kayitlar_durur(tmp_path):
    db = str(tmp_path / "g.db")
    h = SqliteCallHistory(db)
    h.add("c-1", stats(misses=["Ferhat Yıldırım"]))
    h.add("c-2", stats(outcome="operator", misses=["ferhat yıldırım", "Muhasebe Şefi"]))
    h.close()
    h = SqliteCallHistory(db)                       # Gateway yeniden başladı
    r = h.recent()
    assert [x["call_id"] for x in r] == ["c-2", "c-1"] and r[1]["turns"][0]["heard"] == "muhasebeden ayşe hanım"
    assert h.misses()[0] == {"text": "ferhat yıldırım", "count": 2}
    assert h.top_extensions() == [("214", 2)]
    assert h.summary() == {"gun": 1, "sonuc": {"transferred": 1, "operator": 1}, "toplam": 2,
                           "tur": {"kod": 2, "llm": 0}}


def test_saklama_suresi_ve_metin_saklamama(tmp_path):
    h = SqliteCallHistory(str(tmp_path / "g.db"), retention_days=30, keep_texts=False)
    old = (datetime.now() - timedelta(days=40)).isoformat(timespec="seconds")
    h.add("eski", stats(started=old, misses=["x"]))
    h.add("yeni", stats())
    assert h.purge() == 1
    r = h.recent()
    assert [x["call_id"] for x in r] == ["yeni"]
    assert r[0]["turns"][0]["heard"] == "" and r[0]["turns"][0]["reply"] == ""    # metin yazılmadı
    assert r[0]["turns"][0]["actions"] == ["transfer:214"] and r[0]["duration_s"] == 12.5
    assert h.misses() == []


async def test_sik_arananlar_stt_ipucunda_once(tmp_path):
    from pathlib import Path

    from sesli_asistan.gateway.composition import GatewayRuntime
    from sesli_asistan.gateway.config import GatewaySettings

    root = Path(__file__).resolve().parents[2]
    db = str(tmp_path / "g.db")
    h = SqliteCallHistory(db)
    h.add("c-1", CallStats(turns=[TurnRecord("burak", "", ["transfer:215"], 0.1, 0.5)], outcome="transferred",
                           started=datetime.now().isoformat(timespec="seconds")))
    h.close()
    s = GatewaySettings(_env_file=None, directory_path=str(root / "data/rehber_ornek.json"), call_history_db=db)
    rt = GatewayRuntime(s, llm=object(), stt=object(), tts=object())
    await rt.directory.refresh()
    await rt._refresh_top()
    assert rt.top_extensions == ["215"] and rt._hotwords()[0] == "Burak Çelik"
    rt.history.close()
