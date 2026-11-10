"""Satır tablosu → rehber kayıtları: sütunları başlıktan tanıma, satır doğrulama, kişi / birim hattı ayrımı, kimlik
koruma."""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from ..abbreviations import Abbreviations
from ..core.search import has_role_word, search_key
from .common import (
    _NO,
    _YES,
    COLUMNS,
    EXTENSION,
    MAX_ALIASES,
    MAX_FIELD,
    MAX_ROWS,
    MAX_TITLE,
    ImportResult,
    Problem,
    _unescape,
    fold,
)


def map_columns(header: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for i, h in enumerate(header):
        f = fold(h)
        for name, names in COLUMNS.items():
            if f in names and name not in out:
                out[name] = i
                break
    return out


def parse(rows: list[list[str]], existing: Sequence[Any] = (), abbreviations: Abbreviations | None = None
          ) -> ImportResult:
    """Satırlar → rehber kayıtları (Sentra Rehber API biçimi). `existing`: mevcut kaynak rehber (kimlikler korunur).
    `abbreviations`: ad, unvan ve birimdeki kısaltmalar açılır (ADR-030); açılamayanlar `unknown_abbreviations`."""
    res = ImportResult()
    abbr = abbreviations or Abbreviations()
    start, header, cols = _header(rows)
    res.columns = {k: header[i] for k, i in cols.items()}
    body = rows[start + 1:]
    if len(body) > MAX_ROWS:
        raise ValueError(f"en çok {MAX_ROWS} satır")
    ids = _Ids(existing)
    seen: dict[tuple[str, str, str], int] = {}
    repeats: list[tuple[int, int]] = []
    by_ext: dict[str, list[tuple[int, str]]] = {}
    by_name: dict[str, list[tuple[int, str]]] = {}
    now = datetime.now(UTC).isoformat(timespec="seconds")
    for n, row in enumerate(body, start=start + 2):
        if not any(c.strip() for c in row):
            continue
        res.rows += 1
        get = _getter(row, cols, abbr, res.unknown_abbreviations)
        if not any(get(k) for k in ("first_name", "last_name", "full_name", "name_or_title", "title", "department")):
            res.skipped += 1                  # yalnız kod ("*70509", "72695"): kime ait olduğu yok
            continue
        read = _read_row(n, get, abbr.not_names)
        if isinstance(read, Problem):
            res.errors.append(read)
            continue
        rec, who, warning = read
        if warning:
            res.warnings.append(warning)
        key = _key(rec["kind"], who, rec["department"], rec["extension"])
        if key in seen:
            repeats.append((n, seen[key]))     # aynı kayıt tekrar: alınmaz, tek uyarıda özetlenir
            continue
        seen[key] = n
        res.contacts.append({"id": ids.assign(get("id"), key, n), **rec, "updated_at": now, "deleted": False})
        by_ext.setdefault(rec["extension"], []).append((n, who))
        if rec["kind"] == "person":
            by_name.setdefault(search_key(who), []).append((n, rec["extension"]))
    res.warnings += _duplicates(by_ext, by_name)
    if repeats:
        res.skipped += len(repeats)
        res.warnings.append(Problem(repeats[0][0], f"{len(repeats)} satır önceki bir satırın tekrarı, alınmadı (ör. "
                                    + ", ".join(f"{n}. satır = {m}. satır" for n, m in repeats[:3]) + ")"))
    if res.unknown_abbreviations:
        common = ", ".join(f"{k} ({v})" for k, v in res.unknown_abbreviations.most_common(15))
        res.warnings.append(Problem(0, f"açılımı bilinmeyen {len(res.unknown_abbreviations)} kısaltma (kurumun "
                                       f"kısaltma sözlüğüne eklenmeli, ABBREVIATIONS_PATH): {common}"))
    res.warnings.sort(key=lambda p: p.row)
    return res


def _header(rows: list[list[str]]) -> tuple[int, list[str], dict[str, int]]:
    """İlk dolu satır başlıktır; zorunlu sütunlar (dahili; ad-soyad ya da birim) denetlenir."""
    start = next((i for i, r in enumerate(rows) if any(c.strip() for c in r)), None)
    if start is None:
        raise ValueError("dosyada satır yok")
    header = rows[start]
    cols = map_columns(header)
    if "extension" not in cols:
        raise ValueError("dahili numara sütunu bulunamadı (başlık örn. 'Dahili'). Bulunan başlıklar: "
                         + ", ".join(h for h in header if h))
    if not ({"first_name", "last_name"} <= cols.keys() or cols.keys() & {"full_name", "name_or_title", "department"}):
        raise ValueError("ad-soyad (ya da 'Ad Soyad') ya da birim sütunu bulunamadı")
    return start, header, cols


_TEXT_FIELDS = {"first_name", "last_name", "full_name", "name_or_title", "title", "department"}


def _getter(row: list[str], cols: dict[str, int], abbr: Abbreviations, unknown: Counter[str]) -> Callable[[str], str]:
    def get(name: str) -> str:
        i = cols.get(name)
        v = _unescape(" ".join(row[i].split())) if i is not None and i < len(row) else ""
        return abbr.expand(v, unknown) if v and name in _TEXT_FIELDS else v
    return get


def _names(get: Callable[[str], str], not_names: frozenset[str] = frozenset()) -> tuple[str, str, str]:
    """Ad, soyad, unvan. Yalnız "Ad Soyad" sütunu varsa son sözcük soyad. Hat adı sütunu (kişi adı ya da makam):
    kişi adına benziyorsa ad + soyad, değilse unvan (makam hattı)."""
    first, last, title = get("first_name"), get("last_name"), get("title")
    full = get("full_name")
    if not (first or last or full) and (hat := get("name_or_title")):
        if looks_like_person(hat, not_names):
            full = hat
        elif not title:
            title = hat
    if not (first or last) and full:
        parts = full.split()
        first, last = (" ".join(parts[:-1]), parts[-1]) if len(parts) > 1 else (full, "")
    return first, last, title


def looks_like_person(text: str, not_names: frozenset[str] = frozenset()) -> bool:
    """"Ahmet Yılmaz", "Ayşe Nur Kaya": 2–4 sözcük, her biri büyük harfle başlayan harflerden; nokta, rakam, unvan /
    birim sözcüğü yok ("Salon Sorumlusu", "Nöbetçi Amiri" kişi değil)."""
    words = text.split()
    if not 2 <= len(words) <= 4 or any(not w.isalpha() or not w[0].isupper() or (len(w) >= 2 and w.isupper())
                                       for w in words):
        return False
    return not has_role_word(text) and not set(fold(text).split()) & (_NOT_NAMES | not_names)


# hat adlarında geçen, kişi adı olmayan sözcükler (katlanmış): "Salon Sorumlusu", "Nöbetçi Kulübesi"
_NOT_NAMES = {"salon", "sorumlusu", "sorumlu", "nobetci", "kulubesi", "kulube", "konut", "haberci", "kapi", "santral",
              "danisma", "resepsiyon", "guvenlik", "asansor", "toplanti", "salonu", "odasi", "oda", "kantin",
              "yemekhane", "revir", "arsiv", "depo", "atolye", "garaj", "bakanliklar", "giyinme", "sekreterlik",
              "kalem", "kalemi", "belgegecer", "faks", "fax", "makam", "bekleme", "operator", "nizamiye",
              "komutanligi", "baskanligi", "mudurlugu", "daire", "kurulu", "merkezi",
              "ariza", "berber", "kuafor", "terzi", "marangoz", "bahcivan", "cayci", "ocagi", "pastane",
              "kafeterya", "mutfak", "kiler", "camasirci", "temizlikci", "kaloriferci", "elektrikci", "tesisatci",
              "dosemeci", "jeneratorcu", "kaynakci", "boyaci", "kutuphane", "banka", "sigorta",
              "itfaiye", "silahlik", "kres", "dershane", "rezervasyon", "sevk", "kita"}


_FAX = re.compile(r"\b(belgegecer|faks|fax)\b")


def _kind(raw: str, has_name: bool, has_title: bool) -> str | None:
    k = fold(raw)
    if k in ("birim", "birim hatti", "department", "dept", "hat", "genel hat"):
        return "department"
    if k in ("makam", "makam hatti", "unvan hatti", "role"):
        return "role"
    if k in ("", "kisi", "person", "personel", "calisan"):
        return "person" if has_name else "role" if has_title else "department"
    return None


def _read_row(n: int, get: Callable[[str], str],
              not_names: frozenset[str] = frozenset()) -> Problem | tuple[dict[str, Any], str, Problem | None]:
    """Bir satır → (kayıt (kimliksiz), görünen ad, uyarı) ya da hata."""
    first, last, title = _names(get, not_names)
    dept = get("department")
    ext = re.sub(r"[\s\-./]", "", get("extension"))
    kind = _kind(get("kind"), bool(first or last), bool(title))
    if kind is None:
        return Problem(n, f"tür '{get('kind')}' anlaşılmadı (kişi, makam ya da birim)")
    who = f"{first} {last}".strip() or (title if kind == "role" else "") or dept or f"{n}. satır"
    if kind == "person" and not (first and last):
        return Problem(n, f"{who}: kişi kaydında ad ve soyad gerekli (birim hattıysa Tür: birim)")
    if kind == "role":
        if not title:
            return Problem(n, "makam hattında unvan gerekli")
        first = last = ""
    if kind == "department":
        if not dept:
            return Problem(n, "birim hattında birim adı gerekli")
        first = last = ""
    if (problem := _check_fields(n, who, kind, ext, get("extension"), (first, last, dept, title))) is not None:
        return problem
    tr_raw = fold(get("transferable"))
    if tr_raw and tr_raw not in _YES | _NO:
        return Problem(n, f"{who}: aktarılabilir '{get('transferable')}' anlaşılmadı (evet/hayır)")
    aliases, bad = _aliases(get("aliases"))
    dept_aliases, bad2 = _aliases(get("department_aliases"))
    warning = Problem(n, f"{who}: takma ad atlandı: {', '.join((bad + bad2)[:3])}") if bad or bad2 else None
    # belgegeçer / faks hattına sesli çağrı aktarılmaz (sütunda açıkça "evet" yazmıyorsa)
    fax = bool(_FAX.search(fold(f"{who} {title}"))) and tr_raw not in _YES
    rec = {"kind": kind, "first_name": first, "last_name": last, "department": dept, "title": title,
           "extension": ext, "aliases": aliases, "department_aliases": dept_aliases,
           "transferable": tr_raw not in _NO and not fax}
    return rec, who, warning


def _check_fields(n: int, who: str, kind: str, ext: str, ext_raw: str, fields: tuple[str, ...]) -> Problem | None:
    """`fields`: (ad, soyad, birim, unvan); unvan MAX_TITLE'a kadar (açılmış makam adı)."""
    if not ext:
        return Problem(n, f"{who}: dahili numara boş")
    if not EXTENSION.match(ext):
        return Problem(n, f"{who}: dahili '{ext_raw}' 2–6 rakam olmalı (dış hat / cep numarası yazılamaz)")
    limits = (MAX_FIELD, MAX_FIELD, MAX_TITLE, MAX_TITLE)
    if long := [v for v, m in zip(fields, limits, strict=True) if len(v) > m]:
        return Problem(n, f"{who[:40]}: alan çok uzun ({long[0][:20]}…)")
    if kind == "person" and not search_key(who):
        return Problem(n, f"{who}: ad yalnız unvandan oluşuyor")
    return None


class _Ids:
    """Kayıt kimlikleri: dosyadaki geçerli kimlik, yoksa mevcut rehberdeki aynı kaydın kimliği, yoksa içerikten
    türetilen kalıcı kimlik (yeniden yüklemede aynı kalır). "yerel-" ayar ekranının kayıtlarına ayrılmıştır."""

    def __init__(self, existing: Sequence[Any]):
        self.by_key: dict[tuple[str, str, str], str] = {}
        for c in existing:
            self.by_key.setdefault(_key(c.kind, c.full_name, c.department, c.extension), c.id)
        self.used: set[str] = set()

    def assign(self, given: str, key: tuple[str, str, str], n: int) -> str:
        cid = given
        if not re.fullmatch(r"[A-Za-z0-9_.:\-]{1,64}", cid) or cid in self.used or cid.startswith("yerel-"):
            cid = self.by_key.get(key) or "r-" + hashlib.sha1("|".join(key).encode()).hexdigest()[:12]
        if cid in self.used:
            cid = f"{cid}-{n}"
        self.used.add(cid)
        return cid


def contact_record(c: Any) -> dict[str, Any]:
    """Rehber kaydı → rehber dosyasındaki biçim (Sentra Rehber API alanları)."""
    rec = {"id": c.id, "kind": c.kind, "first_name": c.first_name, "last_name": c.last_name,
           "department": c.department, "title": c.title, "extension": c.extension, "aliases": list(c.aliases),
           "department_aliases": list(c.department_aliases), "transferable": c.transferable,
           "updated_at": c.updated_at, "deleted": c.deleted}
    if getattr(c, "unit_path", ()):                       # birim yapısı (ADR-032) yalnız doluysa yazılır
        rec["birim_yolu"] = list(c.unit_path)
    if getattr(c, "category", ""):
        rec["kategori"] = c.category
    return rec


def _key(kind: str, who: str, dept: str, ext: str) -> tuple[str, str, str]:
    """Kaydın kimliği için içerik anahtarı. Makam hattında birim de: aynı unvan ("Belgegeçer") birçok birimde var."""
    return kind, search_key(f"{who} {dept}" if kind == "role" else who), ext


def _duplicates(by_ext: dict[str, list[tuple[int, str]]], by_name: dict[str, list[tuple[int, str]]]) -> list[Problem]:
    out = [Problem(owners[1][0], f"{ext} dahilisi birden çok kayıtta: " + ", ".join(w for _, w in owners[:4]))
           for ext, owners in by_ext.items() if len(owners) > 1]
    out += [Problem(owners[1][0], "aynı ad birden çok kez (dahili " + ", ".join(e for _, e in owners[:4])
                    + "): asistan arayana hangisi olduğunu soracak")
            for owners in by_name.values() if len(owners) > 1]
    return out


def _aliases(raw: str) -> tuple[list[str], list[str]]:
    out: list[str] = []
    bad: list[str] = []
    seen: set[str] = set()
    for a in re.split(r"[;,|/]", raw):
        a = " ".join(a.split())
        if not a:
            continue
        if not 2 <= len(a) <= MAX_FIELD or not search_key(a):
            bad.append(a)
            continue
        if search_key(a) not in seen and len(out) < MAX_ALIASES:
            seen.add(search_key(a))
            out.append(a)
    return out, bad
