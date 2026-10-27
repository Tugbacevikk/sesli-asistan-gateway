"""Mesai saatleri ve tatiller (tasarim.md 5.1.3).

Saf mantık: saat dilimi ve "şimdi" dışarıdan verilir. Biçim (ayar dosyası):

    OFFICE_HOURS = "pzt-cum 08:30-12:30,13:30-18:00; cmt 09:00-13:00"
        gün ya da gün aralığı + virgülle ayrılmış saat aralıkları; noktalı virgülle bloklar. Yazılmayan gün kapalıdır.
        Boş: her zaman açık (kural yok).
    HOLIDAYS = "01-01, 04-23, 05-01, 2027-03-19..2027-03-21, 2026-10-28@13:00"
        AA-GG her yıl; YYYY-AA-GG tek gün; A..B gün aralığı (iki uç dahil); @SS:DD yarım gün (o saatte kapanır).

Günler: pzt sal car per cum cmt paz (Türkçe harfle de yazılabilir: çar, perş, cumartesi…).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sesli_asistan.libs.text.normalizer import turkish_lower

OPEN, AFTER_HOURS, HOLIDAY = "acik", "mesai_disi", "tatil"

_DAYS = {"pzt": 0, "pazartesi": 0, "sal": 1, "sali": 1, "car": 2, "carsamba": 2, "per": 3, "pers": 3,
         "persembe": 3, "cum": 4, "cuma": 4, "cmt": 5, "cumartesi": 5, "paz": 6, "pazar": 6}
_TIME = re.compile(r"^(\d{1,2})[:.](\d{2})$")


_FOLD = str.maketrans("çğıöşü", "cgiosu")


def _day(word: str) -> int:
    key = turkish_lower(word.strip()).translate(_FOLD).replace(" ", "").rstrip(".")
    if key not in _DAYS:
        raise ValueError(f"bilinmeyen gün: {word.strip()!r} (pzt sal car per cum cmt paz)")
    return _DAYS[key]


def _minute(text: str) -> int:
    m = _TIME.match(text.strip())
    if not m:
        raise ValueError(f"saat SS:DD biçiminde olmalı: {text.strip()!r}")
    h, mi = int(m.group(1)), int(m.group(2))
    if h == 24 and mi == 0:
        return 24 * 60
    if not (0 <= h < 24 and 0 <= mi < 60):
        raise ValueError(f"geçersiz saat: {text.strip()!r}")
    return h * 60 + mi


def _date(text: str) -> date:
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        raise ValueError(f"tarih YYYY-AA-GG biçiminde olmalı: {text.strip()!r}") from None


@dataclass(frozen=True)
class OfficeHours:
    weekly: tuple[tuple[tuple[int, int], ...], ...] = ()      # 7 gün × (başlangıç, bitiş) dakika; boş: hep açık
    yearly: dict[tuple[int, int], int | None] = field(default_factory=dict)   # (ay, gün) → kapanış dakikası | None
    dated: dict[date, int | None] = field(default_factory=dict)

    @property
    def always_open(self) -> bool:
        return not self.weekly and not self.yearly and not self.dated

    @classmethod
    def parse(cls, hours: str = "", holidays: str = "") -> OfficeHours:
        weekly: tuple[tuple[tuple[int, int], ...], ...] = ()
        if hours.strip():
            days: list[list[tuple[int, int]]] = [[] for _ in range(7)]
            for block in filter(None, (b.strip() for b in hours.split(";"))):
                head, _, spans = block.partition(" ")
                if not spans.strip():
                    raise ValueError(f"saat aralığı eksik: {block!r} (ör. 'pzt-cum 08:30-18:00')")
                if "-" in head:
                    a, b = (_day(x) for x in head.split("-", 1))
                    sel = list(range(a, b + 1)) if a <= b else [*range(a, 7), *range(0, b + 1)]
                else:
                    sel = [_day(head)]
                for span in filter(None, (s.strip() for s in spans.split(","))):
                    start, sep, end = span.partition("-")
                    if not sep:
                        raise ValueError(f"saat aralığı 'SS:DD-SS:DD' olmalı: {span!r}")
                    s, e = _minute(start), _minute(end)
                    if s >= e:
                        raise ValueError(f"aralık başı sonundan önce olmalı: {span!r}")
                    for d in sel:
                        days[d].append((s, e))
            weekly = tuple(tuple(sorted(d)) for d in days)
        yearly: dict[tuple[int, int], int | None] = {}
        dated: dict[date, int | None] = {}
        for item in filter(None, (x.strip() for x in holidays.replace("\n", ",").split(","))):
            spec, _, close = item.partition("@")
            close_at = _minute(close) if close.strip() else None
            spec = spec.strip()
            if ".." in spec:
                a, b = (_date(x) for x in spec.split("..", 1))
                if b < a:
                    raise ValueError(f"tatil aralığı ters: {item!r}")
                d = a
                while d <= b:
                    dated[d] = close_at
                    d += timedelta(days=1)
            elif re.fullmatch(r"\d{1,2}-\d{1,2}", spec):
                mo, dy = (int(x) for x in spec.split("-"))
                date(2024, mo, dy)  # geçerli mi (29 Şubat dahil)
                yearly[(mo, dy)] = close_at
            else:
                dated[_date(spec)] = close_at
        return cls(weekly, yearly, dated)

    def status(self, now: datetime) -> str:
        """acik | mesai_disi | tatil."""
        if self.always_open:
            return OPEN
        minute = now.hour * 60 + now.minute
        d = now.date()
        key = (d.month, d.day)
        if d in self.dated or key in self.yearly:
            close_at = self.dated[d] if d in self.dated else self.yearly[key]
            if close_at is None or minute >= close_at:
                return HOLIDAY
        if not self.weekly:
            return OPEN
        spans = self.weekly[now.weekday()]
        return OPEN if any(s <= minute < e for s, e in spans) else AFTER_HOURS
