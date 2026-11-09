"""Kurumun STT düzeltmeleri (ADR-033)."""
from __future__ import annotations

import re

from sesli_asistan.libs.text.normalizer import turkish_lower

_FOLD = str.maketrans("çğıöşüâîû", "cgiosuaiu")


def _fold_words(text: str) -> list[str]:
    return re.sub(r"[^\w\s]", " ", turkish_lower(text)).translate(_FOLD).split()


class SttCorrections:
    """Kurumun STT düzeltmeleri (ADR-033): konuşma tanımanın o kuruma özgü adları sık bozduğu biçimler ("masaj
    bakanlığı" → "asayiş başkanlığı", "ama tepe" → "Anıttepe"). Kurum dosyasında (ABBREVIATIONS_PATH)
    `"stt_duzeltmeleri": {"bozuk söz": "doğrusu"}`. Ürün kodunda kuruma özgü düzeltme yoktur: başka kurumda aynı
    sözcük gerçek olabilir ("Bakanlığı"). Eşleme katlanmış sözcüklerle; son sözcüğe gelen ek korunur ("masaj
    bakanlığına" → "asayiş başkanlığına"). Uzun kural önce."""

    def __init__(self, table: dict[str, str] | None = None):
        rules = [(tuple(_fold_words(k)), v.strip()) for k, v in (table or {}).items() if v.strip()]
        self.rules = sorted((r for r in rules if r[0]), key=lambda r: -len(r[0]))

    def __bool__(self) -> bool:
        return bool(self.rules)

    def apply(self, text: str) -> str:
        if not self.rules or not text:
            return text
        toks = text.split()
        folded = [" ".join(_fold_words(t)) for t in toks]
        out: list[str] = []
        i = 0
        while i < len(toks):
            for key, value in self.rules:
                n = len(key)
                if i + n > len(toks) or any(folded[i + j] != key[j] for j in range(n - 1)):
                    continue
                last = folded[i + n - 1]
                if last == key[-1] or (len(key[-1]) >= 4 and last.startswith(key[-1])):
                    out.append(value + last[len(key[-1]):])
                    i += n
                    break
            else:
                out.append(toks[i])
                i += 1
        return " ".join(out)
