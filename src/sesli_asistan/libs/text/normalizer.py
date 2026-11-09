"""Türkçe metin düzeltme.

Kaynak: Vietnamese-speech-to-text-models `infrastructure/normalizer.py` (Türkçe kısmı).
Alınmayan: Vietnamca normalizasyonu (sistem yalnızca Türkçe, ADR-006).

Kurallar:
  - Küçük harf Türkçe kuralıyla (I → ı, İ → i); Python'un `lower()` bunu yanlış yapar.
  - Noktalama silinirken kelime içi kesme ("istanbul'da") ve rakam arası ayırıcı ("3,5") korunur.
  - Çok dilli modeller (Whisper) gürültüde başka alfabeden "kelime" uydurabiliyor; Türkçe Latin
    alfabesiyle yazıldığı için Latin harfi hiç olmayan kelime atılır, Latin kelimeye karışmış
    Kiril benzeri harfler düzeltilir (14 Ekim 2026 gözlemi: "nasыsыn" = nasılsın).
"""
from __future__ import annotations

import re
import unicodedata

# kelime içi kesme ('da) ve rakam arası nokta/virgül (3,5) korunur, diğer işaretler silinir
_PUNCT = re.compile(r"(?<=\w)['’](?=\w)|(?<=\d)[.,](?=\d)|([^\w\s])")

_HOMOGLYPH = str.maketrans({"ы": "ı", "і": "i", "о": "o", "а": "a", "е": "e", "с": "c", "р": "p",
                            "х": "x", "у": "y", "к": "k", "м": "m", "т": "t", "н": "n", "в": "v"})


def turkish_lower(text: str) -> str:
    # "i̇" (i + U+0307 birleşen nokta): ayrıştırılmış "İ" ya da Türkçe dışı küçültmeden kalır; "i" sayılır, yoksa
    # "i̇nsan" ≠ "insan" (ölçüm 04.11.2026: ipuçlu STT çıktısında görüldü)
    return text.replace("I", "ı").replace("İ", "i").lower().replace("i\u0307", "i")


# Türkçe harfleri ASCII'ye katlama (karşılaştırma için; konuşmada / ekranda değil): rehber, sözcük listeleri ve
# STT çıktısı aynı biçime iner ("Şükrü" ~ "sukru"). Tek tablo: önceden beş modülde ayrı ayrı (09.11.2026).
TR_FOLD = str.maketrans("çğıöşüâîû", "cgiosuaiu")


def fold_tr(text: str) -> str:
    """Türkçe küçük harf + katlama ("İŞLEM" → "islem")."""
    return turkish_lower(text).translate(TR_FOLD)


def fold_tr_words(text: str) -> list[str]:
    """Katlanmış sözcükler; noktalama boşluk olur, ekler atılmaz ("Ayşe'nin." → ["ayse", "nin"])."""
    return re.sub(r"[^\w\s]", " ", turkish_lower(text)).translate(TR_FOLD).split()


def strip_punct(text: str) -> str:
    return _PUNCT.sub(lambda m: " " if m.group(1) else m.group(0).replace("’", "'"), text)


def _is_latin(ch: str) -> bool:
    return unicodedata.name(ch, "").startswith("LATIN")


def _latin_fix(w: str) -> str | None:
    """Latin kelime → aynen; karışık → benzer harfler düzeltilir; hiç Latin harf yoksa None."""
    if all(not ch.isalpha() or _is_latin(ch) for ch in w):
        return w
    if not any(ch.isalpha() and _is_latin(ch) for ch in w):
        return None
    return w.translate(_HOMOGLYPH)


class TrNormalizer:
    """lowercase/strip_punct: sistem içi biçim (LLM'e giden, ölçümde karşılaştırılan).
    latin_only: başka alfabeden uydurma kelimeyi süz."""

    def __init__(self, lowercase: bool = True, strip_punct: bool = True, latin_only: bool = True):
        self.lowercase, self.strip_punct, self.latin_only = lowercase, strip_punct, latin_only

    def normalize(self, text: str) -> str:
        text = unicodedata.normalize("NFC", text)
        if self.lowercase:
            text = turkish_lower(text)
        if self.strip_punct:
            text = strip_punct(text)
        words = text.split()
        if self.latin_only:
            words = [f for f in map(_latin_fix, words) if f]
        return " ".join(words)
