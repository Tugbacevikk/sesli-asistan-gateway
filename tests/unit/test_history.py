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


# --- şema geçişleri (tasarim.md 8.4) ---------------------------------------------------------------------------

def test_surumsuz_eski_veritabani_veri_kaybetmeden_gecer(tmp_path):
    import sqlite3

    from sesli_asistan.gateway.adapters.history import sqlite as h

    path = tmp_path / "eski.db"
    db = sqlite3.connect(path)
    db.executescript(h.SCHEMA_V1)                    # ilk sürümün oluşturduğu, user_version = 0 olan veritabanı
    db.execute("INSERT INTO transfers VALUES ('2026-10-01T10:00:00', '214')")
    db.commit()
    db.close()
    hist = SqliteCallHistory(str(path))
    assert hist.schema == h.SCHEMA_VERSION >= 2
    assert hist.top_extensions(days=10000) == [("214", 1)]
    names = {r[0] for r in hist._db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "transfers_started" in names
    hist.close()
    again = SqliteCallHistory(str(path))              # ikinci açılışta geçiş tekrarlanmaz
    assert again.schema == h.SCHEMA_VERSION
    again.close()


def test_yeni_surumun_veritabanina_dokunulmaz(tmp_path, caplog):
    import sqlite3

    from sesli_asistan.gateway.adapters.history import sqlite as h

    path = tmp_path / "yeni.db"
    SqliteCallHistory(str(path)).close()
    db = sqlite3.connect(path)
    db.execute(f"PRAGMA user_version = {h.SCHEMA_VERSION + 3}")
    db.commit()
    db.close()
    hist = SqliteCallHistory(str(path))               # geri dönülmüş sürüm: çalışmaya devam eder
    assert hist.schema == h.SCHEMA_VERSION + 3 and "bu sürümden yeni" in caplog.text
    hist.change_add("a", "yonetici", "1.2.3.4", "ayarlar", ["x"])
    assert hist.changes(1)[0]["ayrinti"] == ["x"]
    hist.close()


def test_yarida_kalan_gecis_geri_alinir(tmp_path, monkeypatch):
    import sqlite3

    import pytest

    from sesli_asistan.gateway.adapters.history import sqlite as h

    path = tmp_path / "g.db"
    SqliteCallHistory(str(path)).close()
    monkeypatch.setattr(h, "MIGRATIONS", (*h.MIGRATIONS, "CREATE TABLE yeni (a INTEGER); BOZUK SQL;"))
    monkeypatch.setattr(h, "SCHEMA_VERSION", len(h.MIGRATIONS))
    with pytest.raises(sqlite3.Error):
        SqliteCallHistory(str(path))
    db = sqlite3.connect(path)
    assert db.execute("PRAGMA user_version").fetchone()[0] == h.SCHEMA_VERSION - 1
    assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='yeni'").fetchone()   # yarım tablo kalmadı
    db.close()
