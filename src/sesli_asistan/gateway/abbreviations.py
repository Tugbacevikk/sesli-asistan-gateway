"""Rehberdeki kısaltmaların açılımı (ADR-030): "Asyş.D.Bşk.lığı Önl.Hiz.Şb.Md." → "Asayiş Daire Başkanlığı Önleyici
Hizmetler Şube Müdürü". Arayan kısaltma söylemez ("asayiş daire başkanlığı"), asistan da teyit sorusunda kısaltma
okuyamaz; bu yüzden rehber yüklenirken açılır ve açık biçim saklanır.

Sözlük: ürünle gelen genel kamu / kurum listesi (`kisaltmalar.json`) + kurumun kendi dosyası (ABBREVIATIONS_PATH,
aynı biçim; aynı anahtar kurumunkini kullanır). Biçim: {"kisaltmalar": {"Bşk.lığı": "Başkanlığı", "Şb.": "Şube"}}.
Anahtar noktasıyla yazılır; büyük / küçük harf ve Türkçe harfler duyarsızdır. Sözlükte olmayan kısaltma olduğu gibi
kalır ve yüklemede raporlanır (kurum sözlüğe ekler). Kuruma özgü kısaltmalar tahmin edilmez: yanlış açılım arayanı
yanlış hatta götürür.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from importlib import resources
from pathlib import Path

from sesli_asistan.libs.text.normalizer import turkish_lower

# kısaltma: harfler + nokta, ardından isteğe bağlı nokta sonrası ek ("Bşk.lığı", "Md.lüğü", "K.lığı")
_TOKEN = re.compile(r"(?<![\wçğıöşüÇĞİÖŞÜâ])([A-Za-zÇĞİÖŞÜçğıöşüâÂ]+\.(?:l[ıiuü]ğ[ıiuü]|[ıiuü]|l[ıiuü]k)?)")


def _key(abbr: str) -> str:
    return turkish_lower(abbr.strip())


class Abbreviations:
    def __init__(self, table: dict[str, str] | None = None):
        self.table = {_key(k): v.strip() for k, v in (table or {}).items() if k.strip() and v.strip()}

    def __len__(self) -> int:
        return len(self.table)

    def expand(self, text: str, unknown: Counter[str] | None = None) -> str:
        """Kısaltmaları açar; sözlükte olmayanlar `unknown`a sayılır ve metinde kalır."""
        if "." not in text:
            return text

        def repl(m: re.Match[str]) -> str:
            tok = m.group(1)
            if (full := self.table.get(_key(tok))) is not None:
                return f" {full} "
            if unknown is not None:
                unknown[tok] += 1
            return f" {tok} "

        return " ".join(_TOKEN.sub(repl, text).split())


def load_abbreviations(path: str = "") -> Abbreviations:
    """Ürünün genel listesi + kurumun dosyası (`path`, boşsa yalnız genel liste)."""
    base = json.loads(resources.files("sesli_asistan.gateway").joinpath("kisaltmalar.json")
                      .read_text(encoding="utf-8"))["kisaltmalar"]
    if path:
        base = {**base, **json.loads(Path(path).read_text(encoding="utf-8")).get("kisaltmalar", {})}
    return Abbreviations(base)
