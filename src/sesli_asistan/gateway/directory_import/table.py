"""Dosya → satır tablosu: .xlsx (yalnız standart kütüphane; boyut, sıkıştırma ve DOCTYPE sınırlı) ve .csv (UTF-8 /
Windows-1254, ayraç ; , sekme)."""
from __future__ import annotations

import csv
import io
import re
import zipfile
from xml.etree import ElementTree as ET

from .common import (
    MAX_FILE,
    MAX_ROWS,
    MAX_UNZIPPED,
)


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
        if data[:2] in (b"\xff\xfe", b"\xfe\xff") or (len(data) > 3 and data[1:2] == b"\x00" != data[:1]):
            text = data.decode("utf-16")        # santral / veritabanı dökümleri ("Unicode metin", sekmeyle ayrılmış)
        else:
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
            shared.extend("".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t"))
                          for si in _xml(z, "xl/sharedStrings.xml").findall("m:si", _NS))
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
