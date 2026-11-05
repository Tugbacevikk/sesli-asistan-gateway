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
        """Kısaltmaları açar; sözlükte olmayanlar `unknown`a sayılır ve metinde kalır. Bitişik kısaltmalarda önce
        en uzun bileşik anahtar ("Bşk.Yrd." → "Başkan Yardımcısı"; tek tek "Başkanı Yardımcısı" olurdu)."""
        if "." not in text:
            return text
        out: list[str] = []
        pos = 0
        for run in self._runs(text):
            out.append(text[pos:run[0].start()])
            out.append(f" {self._expand_run([m.group(1) for m in run], unknown)} ")
            pos = run[-1].end()
        out.append(text[pos:])
        return " ".join("".join(out).split())

    @staticmethod
    def _runs(text: str) -> list[list[re.Match[str]]]:
        """Bitişik kısaltma dizileri ("Asyş.D.Bşk.lığı" → [Asyş., D., Bşk.lığı])."""
        runs: list[list[re.Match[str]]] = []
        for m in _TOKEN.finditer(text):
            if runs and runs[-1][-1].end() == m.start():
                runs[-1].append(m)
            else:
                runs.append([m])
        return runs

    def _expand_run(self, toks: list[str], unknown: Counter[str] | None) -> str:
        words: list[str] = []
        i = 0
        while i < len(toks):
            for n in range(min(3, len(toks) - i), 0, -1):
                if (full := self.table.get(_key("".join(toks[i:i + n])))) is not None:
                    words.append(full)
                    i += n
                    break
            else:
                if unknown is not None:
                    unknown[toks[i]] += 1
                words.append(toks[i])
                i += 1
        return " ".join(words)


def load_abbreviations(path: str = "") -> Abbreviations:
    """Ürünün genel listesi + kurumun dosyası (`path`, boşsa yalnız genel liste)."""
    base = json.loads(resources.files("sesli_asistan.gateway").joinpath("kisaltmalar.json")
                      .read_text(encoding="utf-8"))["kisaltmalar"]
    if path:
        base = {**base, **json.loads(Path(path).read_text(encoding="utf-8")).get("kisaltmalar", {})}
    return Abbreviations(base)
