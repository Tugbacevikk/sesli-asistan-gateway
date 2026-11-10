"""Rehber yüklemenin ortak parçaları: boyut sınırları, tanınan sütunlar, Türkçe katlama, Excel formül kaçışı, sorun
ve sonuç tipleri."""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

MAX_FILE = 5 * 1024 * 1024


MAX_ROWS = 20000


MAX_UNZIPPED = 60 * 1024 * 1024


MAX_FIELD = 60


MAX_TITLE = 160      # makam hattının açılmış unvanı uzun olabilir ("Asayiş Daire Başkanlığı Önleyici Hizmetler …")


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
                  "numara", "tel", "telefon", "dahili telefon", "access code", "erisim kodu", "kod", "dahili kod"),
    # santral dökümlerindeki hat adı: kişi adı ya da makam ("Bşk.Yrd. Belgegeçer"); satır satır ayırt edilir
    "name_or_title": ("sip name", "hat adi", "gorunen ad", "display name", "caller id", "makam", "makam adi"),
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
    skipped: int = 0                                          # adı, unvanı ve birimi boş satırlar (sessizce)
    unknown_abbreviations: Counter[str] = field(default_factory=Counter)   # sözlükte olmayan kısaltmalar


_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def _excel_safe(v: str) -> str:
    """CSV formül enjeksiyonu: '=' '+' '-' '@' ile başlayan hücre Excel'de formül olarak çalışmasın."""
    return f"'{v}" if v.startswith(_FORMULA) else v


def _unescape(v: str) -> str:
    return v[1:] if v.startswith("'") and v[1:].startswith(_FORMULA) else v
