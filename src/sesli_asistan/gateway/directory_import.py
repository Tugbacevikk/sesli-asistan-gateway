"""Rehber yükleme (tasarim.md 13.2): müşterinin Excel / CSV rehberi → rehber dosyası (DIRECTORY_PATH).

Akış: dosya okunur → sütunlar başlıktan tanınır → her satır doğrulanır → önizleme (eklenen / silinen / değişen,
hatalı satırlar, uyarılar) → yönetici onaylar → rehber dosyası atomik yazılır (önceki sürüm `.onceki` olarak
saklanır, tek adımda geri alınabilir) → rehber yeniden okunur. Yerel takma adlar kayıt kimliğine bağlıdır: aynı
kişi (tür + ad + dahili) yeniden yüklendiğinde kimliği korunur, takma adları kopmaz.

Biçimler:
  - .xlsx (Excel 2007+): ilk sayfa. Yalnız standart kütüphane (zipfile + ElementTree): ek bağımlılık yok; boyut,
    sıkıştırma oranı ve DOCTYPE (XML varlık saldırıları) sınırlanır.
  - .csv: UTF-8 (BOM'lu ya da değil) ya da Windows-1254 (Türkçe Excel'in "CSV" kaydı); ayraç ; , ya da sekme.
  - Eski .xls kabul edilmez: "Farklı kaydet → .xlsx" denir.

Sütun adları büyük/küçük harf ve Türkçe karakterden bağımsızdır (ör. "Adı", "AD", "isim"; "Dahili No", "Extension").
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from xml.etree import ElementTree as ET

from .core.search import search_key

MAX_FILE = 5 * 1024 * 1024
MAX_ROWS = 20000
MAX_UNZIPPED = 60 * 1024 * 1024
MAX_FIELD = 60
MAX_ALIASES = 10
EXTENSION = re.compile(r"^\d{2,6}$")   # yalnız dahili numara: dış hat / cep numarası aktarılmaz (aktarma güvenliği)

# alan → tanınan başlıklar (fold() edilmiş)
COLUMNS: dict[str, tuple[str, ...]] = {
    "id": ("id", "kimlik", "kayit no", "kayit id"),
    "full_name": ("ad soyad", "adi soyadi", "ad soyadi", "isim soyisim", "isim soyad", "adsoyad", "name",
                  "full name", "kisi", "personel"),
    "first_name": ("ad", "adi", "isim", "first name", "firstname", "given name"),
    "last_name": ("soyad", "soyadi", "soyisim", "last name", "lastname", "surname", "family name"),
    "department": ("birim", "birimi", "departman", "departmani", "bolum", "bolumu", "department", "servis",
                   "mudurluk", "ekip"),
    "title": ("unvan", "unvani", "gorev", "gorevi", "pozisyon", "title", "job title"),
    "extension": ("dahili", "dahili no", "dahili numara", "dahili numarasi", "dahili tel", "extension", "ext",
                  "numara", "tel", "telefon", "dahili telefon"),
    "kind": ("tur", "turu", "kayit turu", "tip", "kind", "type"),
    "aliases": ("takma ad", "takma adlar", "diger adlar", "aliases", "lakap"),
    "department_aliases": ("birim takma ad", "birim takma adlari", "birim diger adlar", "department aliases"),
    "transferable": ("aktarilabilir", "aktarim", "aktarma", "transferable", "aktif"),
}
EXPORT_HEADER = ["id", "Tür", "Ad", "Soyad", "Birim", "Unvan", "Dahili", "Takma adlar", "Birim takma adları",
                 "Aktarılabilir"]
_YES = {"evet", "e", "1", "true", "yes", "var", "x", "aktif"}
_NO = {"hayir", "h", "0", "false", "no", "yok", "pasif"}
_TR = str.maketrans("çğıöşüâîûÇĞİÖŞÜÂÎÛI", "cgiosuaiuCGIOSUAIUI")


def fold(text: str) -> str:
    """Başlık karşılaştırması: Türkçe karakterler sadeleşir, küçük harf, noktalama boşluk olur."""
    t = str(text).translate(_TR).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


@dataclass
class Problem:
    row: int          # dosyadaki satır numarası (başlık 1. satır)
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {"row": self.row, "message": self.message}


@dataclass
class ImportResult:
    contacts: list[dict[str, Any]] = field(default_factory=list)
    errors: list[Problem] = field(default_factory=list)       # satır alınmadı
    warnings: list[Problem] = field(default_factory=list)     # satır alındı, dikkat
    columns: dict[str, str] = field(default_factory=dict)     # alan → dosyadaki başlık
    rows: int = 0


# --- dosya okuma ----------------------------------------------------------------------------------------------

def read_table(data: bytes, filename: str = "") -> list[list[str]]:
    if len(data) > MAX_FILE:
        raise ValueError(f"dosya en çok {MAX_FILE // (1024 * 1024)} MB olabilir")
    if not data.strip():
        raise ValueError("dosya boş")
    if data[:4] == b"PK\x03\x04":
        return _read_xlsx(data)
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" or filename.lower().endswith(".xls"):
        raise ValueError("eski Excel biçimi (.xls) okunmuyor: Excel'de 'Farklı Kaydet → Excel Çalışma Kitabı "
                         "(.xlsx)' ya da 'CSV UTF-8' seçin")
    return _read_csv(data)


def _read_csv(data: bytes) -> list[list[str]]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1254", errors="replace")   # Türkçe Windows Excel'in varsayılan CSV kaydı
    if "\x00" in text:
        raise ValueError("dosya metin (CSV) ya da .xlsx değil")
    first = text.split("\n", 1)[0]
    delim = max((";", ",", "\t"), key=first.count)
    return [[c.strip() for c in row] for row in csv.reader(io.StringIO(text), delimiter=delim)]


_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
       "rel": "http://schemas.openxmlformats.org/package/2006/relationships"}


def _xml(z: zipfile.ZipFile, name: str) -> ET.Element:
    raw = z.read(name)
    if b"<!DOCTYPE" in raw[:4096].upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("Excel dosyası beklenmeyen XML içeriyor (DOCTYPE/ENTITY)")
    return ET.fromstring(raw)


def _read_xlsx(data: bytes) -> list[list[str]]:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("Excel dosyası bozuk") from None
    with z:
        infos = z.infolist()
        if len(infos) > 2000 or sum(i.file_size for i in infos) > MAX_UNZIPPED:
            raise ValueError("Excel dosyası çok büyük (açılmış hâli sınırı aşıyor)")
        names = {i.filename for i in infos}
        if "xl/workbook.xml" not in names:
            raise ValueError("dosya Excel çalışma kitabı (.xlsx) değil")
        wb = _xml(z, "xl/workbook.xml")
        sheet = wb.find("m:sheets/m:sheet", _NS)
        if sheet is None:
            raise ValueError("Excel dosyasında sayfa yok")
        rid = sheet.get(f"{{{_NS['r']}}}id", "")
        target = "worksheets/sheet1.xml"
        if "xl/_rels/workbook.xml.rels" in names:
            for rel in _xml(z, "xl/_rels/workbook.xml.rels").findall("rel:Relationship", _NS):
                if rel.get("Id") == rid:
                    target = rel.get("Target", target)
        path = target.lstrip("/") if target.startswith("/") else f"xl/{target}"
        if path not in names:
            raise ValueError("Excel dosyasında ilk sayfa bulunamadı")
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            for si in _xml(z, "xl/sharedStrings.xml").findall("m:si", _NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t")))
        rows: list[list[str]] = []
        for row in _xml(z, path).iter(f"{{{_NS['m']}}}row"):
            cells: dict[int, str] = {}
            for c in row.findall("m:c", _NS):
                col = _col_index(c.get("r", "")) if c.get("r") else len(cells)
                cells[col] = _cell_value(c, shared)
            if len(rows) > MAX_ROWS + 1:
                raise ValueError(f"en çok {MAX_ROWS} satır")
            width = max(cells) + 1 if cells else 0
            n = int(row.get("r") or len(rows) + 1)
            while len(rows) < n - 1:          # boş satırlar atlanmışsa satır numaraları kaymasın
                rows.append([])
            rows.append([cells.get(i, "").strip() for i in range(width)])
        return rows


def _col_index(ref: str) -> int:
    n = 0
    for ch in ref:
        if not ch.isalpha():
            break
        n = n * 26 + ord(ch.upper()) - 64
    return n - 1


def _cell_value(c: ET.Element, shared: list[str]) -> str:
    t = c.get("t", "n")
    if t == "inlineStr":
        return "".join(x.text or "" for x in c.iter(f"{{{_NS['m']}}}t"))
    v = c.find("m:v", _NS)
    raw = v.text if v is not None and v.text is not None else ""
    if t == "s":
        try:
            return shared[int(raw)]
        except (ValueError, IndexError):
            return ""
    if t == "b":
        return "evet" if raw == "1" else "hayır"
    if t == "n" and re.fullmatch(r"-?\d+\.0+", raw):    # Excel sayıları ondalıklı saklar: 214.0 → 214
        return raw.split(".")[0]
    return raw


# --- sütunlar ve satırlar -------------------------------------------------------------------------------------

def map_columns(header: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for i, h in enumerate(header):
        f = fold(h)
        for name, names in COLUMNS.items():
            if f in names and name not in out:
                out[name] = i
                break
    return out


def parse(rows: list[list[str]], existing: Sequence[Any] = ()) -> ImportResult:
    """Satırlar → rehber kayıtları (Sentra Rehber API biçimi). `existing`: mevcut kaynak rehber (kimlikler korunur)."""
    res = ImportResult()
    start = next((i for i, r in enumerate(rows) if any(c.strip() for c in r)), None)
    if start is None:
        raise ValueError("dosyada satır yok")
    header = rows[start]
    cols = map_columns(header)
    res.columns = {k: header[i] for k, i in cols.items()}
    if "extension" not in cols:
        raise ValueError("dahili numara sütunu bulunamadı (başlık örn. 'Dahili'). Bulunan başlıklar: "
                         + ", ".join(h for h in header if h))
    if not ({"first_name", "last_name"} <= cols.keys() or "full_name" in cols or "department" in cols):
        raise ValueError("ad-soyad (ya da 'Ad Soyad') ya da birim sütunu bulunamadı")
    body = rows[start + 1:]
    if len(body) > MAX_ROWS:
        raise ValueError(f"en çok {MAX_ROWS} satır")

    ids_by_key: dict[tuple[str, str, str], str] = {}
    for c in existing:
        ids_by_key.setdefault((c.kind, search_key(c.full_name), c.extension), c.id)
    seen: dict[tuple[str, str, str], int] = {}
    used_ids: set[str] = set()
    by_ext: dict[str, list[tuple[int, str]]] = {}
    by_name: dict[str, list[tuple[int, str]]] = {}
    now = datetime.now(UTC).isoformat(timespec="seconds")

    for n, row in enumerate(body, start=start + 2):
        def get(name: str, row: list[str] = row) -> str:
            i = cols.get(name)
            return _unescape(" ".join(row[i].split())) if i is not None and i < len(row) else ""

        if not any(c.strip() for c in row):
            continue
        res.rows += 1
        first, last = get("first_name"), get("last_name")
        if not (first or last) and (full := get("full_name")):
            parts = full.split()
            first, last = " ".join(parts[:-1]), parts[-1] if len(parts) > 1 else ""
            if len(parts) == 1:
                first = full
        dept, title = get("department"), get("title")
        ext = re.sub(r"[\s\-./]", "", get("extension"))
        kind_raw = fold(get("kind"))
        if kind_raw in ("birim", "birim hatti", "department", "dept", "hat", "genel hat"):
            kind = "department"
        elif kind_raw in ("", "kisi", "person", "personel", "calisan"):
            kind = "person" if (first or last) else "department"
        else:
            res.errors.append(Problem(n, f"tür '{get('kind')}' anlaşılmadı (kişi ya da birim)"))
            continue
        who = f"{first} {last}".strip() or dept or f"{n}. satır"
        if kind == "person" and not (first and last):
            res.errors.append(Problem(n, f"{who}: kişi kaydında ad ve soyad gerekli (birim hattıysa Tür: birim)"))
            continue
        if kind == "department":
            if not dept:
                res.errors.append(Problem(n, "birim hattında birim adı gerekli"))
                continue
            first = last = ""
        if not ext:
            res.errors.append(Problem(n, f"{who}: dahili numara boş"))
            continue
        if not EXTENSION.match(ext):
            res.errors.append(Problem(n, f"{who}: dahili '{get('extension')}' 2–6 rakam olmalı "
                                         "(dış hat / cep numarası yazılamaz)"))
            continue
        if long := [v for v in (first, last, dept, title) if len(v) > MAX_FIELD]:
            res.errors.append(Problem(n, f"{who}: alan en çok {MAX_FIELD} karakter ({long[0][:20]}…)"))
            continue
        if kind == "person" and not search_key(who):
            res.errors.append(Problem(n, f"{who}: ad yalnız unvandan oluşuyor"))
            continue
        aliases, bad = _aliases(get("aliases"))
        dept_aliases, bad2 = _aliases(get("department_aliases"))
        if bad or bad2:
            res.warnings.append(Problem(n, f"{who}: takma ad atlandı: {', '.join((bad + bad2)[:3])}"))
        tr_raw = fold(get("transferable"))
        if tr_raw and tr_raw not in _YES | _NO:
            res.errors.append(Problem(n, f"{who}: aktarılabilir '{get('transferable')}' anlaşılmadı (evet/hayır)"))
            continue
        transferable = tr_raw not in _NO

        key = (kind, search_key(who), ext)
        if key in seen:
            res.errors.append(Problem(n, f"{who} ({ext}) {seen[key]}. satırın tekrarı"))
            continue
        seen[key] = n
        cid = get("id")
        if not re.fullmatch(r"[A-Za-z0-9_.:\-]{1,64}", cid) or cid in used_ids or cid.startswith("yerel-"):
            cid = ids_by_key.get(key) or "r-" + hashlib.sha1("|".join(key).encode()).hexdigest()[:12]
        if cid in used_ids:
            cid = f"{cid}-{n}"
        used_ids.add(cid)
        res.contacts.append({"id": cid, "kind": kind, "first_name": first, "last_name": last, "department": dept,
                             "title": title, "extension": ext, "aliases": aliases,
                             "department_aliases": dept_aliases, "transferable": transferable,
                             "updated_at": now, "deleted": False})
        by_ext.setdefault(ext, []).append((n, who))
        if kind == "person":
            by_name.setdefault(search_key(who), []).append((n, ext))

    for ext, owners in by_ext.items():
        if len(owners) > 1:
            res.warnings.append(Problem(owners[1][0], f"{ext} dahilisi birden çok kayıtta: "
                                                      + ", ".join(w for _, w in owners[:4])))
    for owners in by_name.values():
        if len(owners) > 1:
            res.warnings.append(Problem(owners[1][0], "aynı ad birden çok kez (dahili "
                                        + ", ".join(e for _, e in owners[:4])
                                        + "): asistan arayana hangisi olduğunu soracak"))
    res.warnings.sort(key=lambda p: p.row)
    return res


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


# --- karşılaştırma ve dışa aktarma ----------------------------------------------------------------------------

_COMPARED = ("kind", "first_name", "last_name", "department", "title", "extension", "transferable")


def diff(old: Sequence[Any], new: list[dict[str, Any]]) -> dict[str, Any]:
    """Önizleme için: eklenen / silinen / değişen kayıtlar (kimliğe göre)."""
    o = {c.id: c for c in old}
    nw = {c["id"]: c for c in new}

    def name(c: Any) -> str:
        if isinstance(c, dict):
            return f"{c['first_name']} {c['last_name']}".strip() or c["department"]
        return str(c.full_name)

    def changed(a: Any, b: dict[str, Any]) -> list[str]:
        out = [k for k in _COMPARED if str(getattr(a, k)) != str(b[k])]
        if sorted(a.aliases) != sorted(b["aliases"]) or sorted(a.department_aliases) != sorted(
                b["department_aliases"]):
            out.append("aliases")
        return out

    added = [nw[k] for k in nw.keys() - o.keys()]
    removed = [o[k] for k in o.keys() - nw.keys()]
    changes = [(nw[k], changed(o[k], nw[k])) for k in nw.keys() & o.keys()]
    changes = [(c, f) for c, f in changes if f]
    unchanged = len(nw.keys() & o.keys()) - len(changes)

    def brief(c: Any) -> dict[str, str]:
        if isinstance(c, dict):
            return {"name": name(c), "department": c["department"], "extension": c["extension"]}
        return {"name": name(c), "department": c.department, "extension": c.extension}

    return {"added": len(added), "removed": len(removed), "changed": len(changes), "unchanged": unchanged,
            "examples": {"added": [brief(c) for c in sorted(added, key=name)[:10]],
                         "removed": [brief(c) for c in sorted(removed, key=name)[:10]],
                         "changed": [{**brief(c), "fields": f} for c, f in sorted(changes,
                                                                                  key=lambda x: name(x[0]))[:10]]}}


def keep_unchanged_timestamps(old: Sequence[Any], new: list[dict[str, Any]]) -> None:
    """Değişmeyen kayıtların updated_at'i korunur (rehber API'sindeki artımlı güncelleme anlamı bozulmasın)."""
    o = {c.id: c for c in old}
    for c in new:
        prev = o.get(c["id"])
        if prev is not None and prev.updated_at and all(str(getattr(prev, k)) == str(c[k]) for k in _COMPARED) \
                and sorted(prev.aliases) == sorted(c["aliases"]) \
                and sorted(prev.department_aliases) == sorted(c["department_aliases"]):
            c["updated_at"] = prev.updated_at


def export_csv(contacts: Sequence[Any]) -> bytes:
    """Mevcut rehber → CSV (Excel'de açılır: UTF-8 BOM, ';' ayraç). Düzenlenip geri yüklenebilir (id korunur)."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(EXPORT_HEADER)
    for c in sorted(contacts, key=lambda c: (c.department.casefold(), c.kind != "department",
                                             c.full_name.casefold())):
        row = [c.id, "birim" if c.kind == "department" else "kişi", c.first_name, c.last_name, c.department,
               c.title, c.extension, ", ".join(c.aliases), ", ".join(c.department_aliases),
               "evet" if c.transferable else "hayır"]
        w.writerow([_excel_safe(v) for v in row])
    return ("﻿" + buf.getvalue()).encode("utf-8")


_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def _excel_safe(v: str) -> str:
    """CSV formül enjeksiyonu: '=' '+' '-' '@' ile başlayan hücre Excel'de formül olarak çalışmasın."""
    return f"'{v}" if v.startswith(_FORMULA) else v


def _unescape(v: str) -> str:
    return v[1:] if v.startswith("'") and v[1:].startswith(_FORMULA) else v


def template_csv() -> bytes:
    """Boş şablon (örnek iki satırla)."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(EXPORT_HEADER)
    w.writerow(["", "birim", "", "", "Muhasebe", "Birim hattı", "210", "", "Finans, Mali İşler", "evet"])
    w.writerow(["", "kişi", "Ayşe", "Yılmaz", "Muhasebe", "Muhasebe Uzmanı", "214", "Ayşe Hanım", "", "evet"])
    return ("﻿" + buf.getvalue()).encode("utf-8")
