"""Kalıcı çağrı geçmişi (SQLite, standart kütüphane).

İzleme ekranı ve raporlar için: biten her çağrının özeti (sonuç, süre, turlar, bulunamayan aramalar). Gateway yeniden
başlasa da kalır. Kişisel veri içerir (arayanın söyledikleri): `CALL_HISTORY_DAYS` gün sonra silinir;
`CALL_HISTORY_TEXTS=false` ile konuşma metinleri hiç yazılmaz (yalnız sonuç ve süreler). KVKK: tasarim.md 10.4.
Ses kaydı tutulmaz. Arayan numarası saklanmaz.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import sqlite3
import threading
from collections import Counter, deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Şema geçişleri (tasarim.md 8.4): sıra numarası = PRAGMA user_version. Yalnız SONA eklenir, eskisi değiştirilmez.
# Kural: geçişler yalnız EKLER (tablo, sütun, dizin); sütun silmez / yeniden adlandırmaz. Böylece `install.sh --geri-al`
# ile eski sürüme dönülünce eski kod yeni şemalı veritabanıyla çalışmaya devam eder.
# 1: ilk şema (sürüm numarası olmadan oluşturulmuş veritabanları da 0 → 1 geçişiyle aynı yere gelir: IF NOT EXISTS)
SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS calls (
    call_id    TEXT NOT NULL,
    started    TEXT NOT NULL,
    channel    TEXT,
    outcome    TEXT,
    reason     TEXT,
    duration_s REAL,
    turns      INTEGER,
    data       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS calls_started ON calls(started);
CREATE TABLE IF NOT EXISTS misses (started TEXT NOT NULL, text TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS misses_started ON misses(started);
CREATE TABLE IF NOT EXISTS transfers (started TEXT NOT NULL, extension TEXT NOT NULL);
-- ayar / rehber değişiklikleri (denetim kaydı): kim, ne zaman, nereden, ne
CREATE TABLE IF NOT EXISTS changes (
    ts TEXT NOT NULL, who TEXT NOT NULL, role TEXT NOT NULL, ip TEXT NOT NULL,
    action TEXT NOT NULL, detail TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS changes_ts ON changes(ts);
"""
MIGRATIONS: tuple[str, ...] = (
    SCHEMA_V1,
    # 2: en çok aranan dahililer sorgusu (top_extensions) ve temizlik tarih aralığıyla süzer
    "CREATE INDEX IF NOT EXISTS transfers_started ON transfers(started);",
)
SCHEMA_VERSION = len(MIGRATIONS)


def migrate(db: sqlite3.Connection) -> tuple[int, int]:
    """Bekleyen geçişleri sırayla, her birini ayrı işlemde uygular. (önceki, şimdiki) sürüm döner.

    Geçiş yarıda kalırsa (disk dolu, elektrik) işlem geri alınır, sürüm artmaz: bir sonraki açılışta yeniden denenir.
    Veritabanı bu koddan YENİ bir sürümle yazılmışsa (geri dönüş) dokunulmaz, uyarı loglanır: geçişler yalnız ekleme
    yaptığından eski kod çalışmaya devam eder."""
    before = db.execute("PRAGMA user_version").fetchone()[0]
    if before > SCHEMA_VERSION:
        log.warning("çağrı geçmişi şeması (%d) bu sürümden yeni (%d): önceki sürüme dönülmüş; geçiş yapılmadı",
                    before, SCHEMA_VERSION)
        return before, before
    for version in range(before + 1, SCHEMA_VERSION + 1):
        try:
            db.executescript(f"BEGIN IMMEDIATE;\n{MIGRATIONS[version - 1]}\nPRAGMA user_version = {version};\nCOMMIT;")
        except sqlite3.Error:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        log.info("çağrı geçmişi şeması %d → %d", version - 1, version)
    return before, SCHEMA_VERSION
CHANGES_DAYS = 365          # denetim kaydı çağrı geçmişinden uzun tutulur


def _turn_paths(calls: Any) -> dict[str, int]:
    """Arayan turlarının kaçı LLM'e gitti, kaçı kodla çözüldü (LLM gerekli mi sorusu için)."""
    out = {"kod": 0, "llm": 0}
    for turns in calls:
        for t in turns:
            if t.get("heard") and not str(t["heard"]).startswith("["):
                out["llm" if t.get("llm_calls") else "kod"] += 1
    return out


class SqliteCallHistory:
    def __init__(self, path: str, retention_days: int = 30, keep_texts: bool = True):
        self.path, self.retention_days, self.keep_texts = path, retention_days, keep_texts
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self.schema = migrate(self._db)[1]
        log.info("çağrı geçmişi: %s (%d gün, metinler %s)", path, retention_days,
                 "saklanıyor" if keep_texts else "saklanmıyor")

    def add(self, call_id: str, stats: Any) -> None:
        data = dataclasses.asdict(stats)
        if not self.keep_texts:
            for t in data["turns"]:
                t["heard"] = t["reply"] = ""
        started = data["started"] or datetime.now().isoformat(timespec="seconds")
        with self._lock, self._db:
            self._db.execute("INSERT INTO calls VALUES (?,?,?,?,?,?,?,?)",
                             (call_id, started, data["channel"], data["outcome"], data["reason"],
                              data["duration_s"], len(data["turns"]), json.dumps(data, ensure_ascii=False)))
            self._db.executemany("INSERT INTO misses VALUES (?,?)", [(started, m.casefold()) for m in data["misses"]])
            self._db.executemany("INSERT INTO transfers VALUES (?,?)",
                                 [(started, a.split(":", 1)[1]) for t in data["turns"] for a in t["actions"]
                                  if a.startswith("transfer:")])

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT call_id, data FROM calls ORDER BY started DESC, rowid DESC LIMIT ?",
                                    (limit,)).fetchall()
        return [{"call_id": cid, **json.loads(d)} for cid, d in rows]

    def misses(self, limit: int = 20, days: int | None = None) -> list[dict[str, Any]]:
        since = (datetime.now() - timedelta(days=days or self.retention_days)).isoformat(timespec="seconds")
        with self._lock:
            rows = self._db.execute("SELECT text, COUNT(*) c FROM misses WHERE started >= ? GROUP BY text "
                                    "ORDER BY c DESC LIMIT ?", (since, limit)).fetchall()
        return [{"text": t, "count": c} for t, c in rows]

    def change_add(self, who: str, role: str, ip: str, action: str, detail: list[str]) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT INTO changes VALUES (?,?,?,?,?,?)",
                             (datetime.now().isoformat(timespec="seconds"), who, role, ip, action,
                              json.dumps(detail, ensure_ascii=False)))

    def changes(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT ts, who, role, ip, action, detail FROM changes ORDER BY ts DESC, rowid DESC"
                                    " LIMIT ?", (limit,)).fetchall()
        return [{"ts": r[0], "kim": r[1], "rol": r[2], "ip": r[3], "islem": r[4], "ayrinti": json.loads(r[5])}
                for r in rows]

    def top_extensions(self, limit: int = 50, days: int = 30) -> list[tuple[str, int]]:
        """En çok aktarılan dahililer (Whisper ipucu önceliği için)."""
        since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        with self._lock:
            return self._db.execute("SELECT extension, COUNT(*) c FROM transfers WHERE started >= ? "
                                    "GROUP BY extension ORDER BY c DESC LIMIT ?", (since, limit)).fetchall()

    def summary(self, days: int = 1) -> dict[str, Any]:
        since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        with self._lock:
            rows = self._db.execute("SELECT outcome, COUNT(*) FROM calls WHERE started >= ? GROUP BY outcome",
                                    (since,)).fetchall()
            datas = [r[0] for r in self._db.execute("SELECT data FROM calls WHERE started >= ?", (since,))]
        turns = _turn_paths(json.loads(d)["turns"] for d in datas)
        return {"gun": days, "sonuc": dict(rows), "toplam": sum(c for _, c in rows), "tur": turns}

    def purge(self) -> int:
        cutoff = (datetime.now() - timedelta(days=self.retention_days)).isoformat(timespec="seconds")
        with self._lock, self._db:
            n = self._db.execute("DELETE FROM calls WHERE started < ?", (cutoff,)).rowcount
            self._db.execute("DELETE FROM misses WHERE started < ?", (cutoff,))
            self._db.execute("DELETE FROM transfers WHERE started < ?", (cutoff,))
            old = (datetime.now() - timedelta(days=CHANGES_DAYS)).isoformat(timespec="seconds")
            self._db.execute("DELETE FROM changes WHERE ts < ?", (old,))
        if n:
            log.info("çağrı geçmişi: %d eski kayıt silindi (%d günden eski)", n, self.retention_days)
        return n

    def close(self) -> None:
        with self._lock:
            self._db.close()


class MemoryCallHistory:
    """Veritabanı kapalıyken (CALL_HISTORY_DB boş): bellekteki son çağrılar."""

    def __init__(self, manager: Any):
        self.manager = manager
        self._changes: deque[dict[str, Any]] = deque(maxlen=200)

    def add(self, call_id: str, stats: Any) -> None:
        pass   # yönetici zaten bellekte tutuyor

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        return [{"call_id": cid, **dataclasses.asdict(st)} for cid, st in reversed(self.manager.results)][:limit]

    def misses(self, limit: int = 20, days: int | None = None) -> list[dict[str, Any]]:
        c = Counter(m.casefold() for _, st in self.manager.results for m in st.misses)
        return [{"text": t, "count": n} for t, n in c.most_common(limit)]

    def top_extensions(self, limit: int = 50, days: int = 30) -> list[tuple[str, int]]:
        c = Counter(a.split(":", 1)[1] for _, st in self.manager.results for t in st.turns for a in t.actions
                    if a.startswith("transfer:"))
        return c.most_common(limit)

    def change_add(self, who: str, role: str, ip: str, action: str, detail: list[str]) -> None:
        self._changes.appendleft({"ts": datetime.now().isoformat(timespec="seconds"), "kim": who, "rol": role,
                                  "ip": ip, "islem": action, "ayrinti": detail})

    def changes(self, limit: int = 50) -> list[dict[str, Any]]:
        return list(self._changes)[:limit]

    def summary(self, days: int = 1) -> dict[str, Any]:
        c = Counter(st.outcome for _, st in self.manager.results)
        turns = _turn_paths([dataclasses.asdict(t) for t in st.turns] for _, st in self.manager.results)
        return {"gun": days, "sonuc": dict(c), "toplam": sum(c.values()), "tur": turns}

    def purge(self) -> int:
        return 0

    def close(self) -> None:
        pass
