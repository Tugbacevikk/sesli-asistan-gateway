"""“Söylenen sözcük şu sözcük mü?” kararı tek yerde. Sözcükler katlanmış (`search_key` / `fold_words`) gelir.

    same_word(a, b)               aynı sözcüğün ekli biçimleri (iki yönlü): "komutanıyla" ~ "komutanı"
    same_word(a, b, fuzzy=ESIK)   üstelik STT yazım hatası payı: ekler atılmış benzerlik ≥ ESIK
    spoken_as(said, target)       cevaptaki sözcük hedefin kendisi ya da ekli biçimi ("muhasebedeki" → "muhasebe"),
                                  ya da eki atılınca hedefe yakın (CHOICE_WORD): seçim cevapları için

Arama sıralaması (`similarity` puanı, `DirectoryIndex`) ayrı işdir: orada ikili karar değil puan gerekir.
"""
from __future__ import annotations

from .search import similarity, strip_suffix
from .thresholds import CHOICE_WORD

_SOFTENING = {("p", "b"), ("t", "d"), ("k", "g")}    # katlanmış: kitap/kitabı, kanat/kanadı, renk/rengi
_VOWELS = set("aeiou")                              # katlanmış ünlüler (ı→i, ö→o, ü→u)


def same_word(a: str, b: str, fuzzy: float | None = None) -> bool:
    """Aynı sözcüğün ekli biçimleri: biri ötekinin başı ("komutanıyla" ~ "komutanı", "kaymakamı" ~ "kaymakamlığı").
    Türkçe ek atmak yerine ortak baş: katmanlı eklerde ("yardımcısına") ek atıcı kökü tutarlı bulamıyor. Kısa sözcüğün
    son harfi ayrışabilir, ama yalnız ek ünlüsü ("kaymakamı") ya da ünsüz yumuşaması ("kitap" ~ "kitabı") ise: şirket
    rehberinde "satış" ~ "satınalma", "yönetim" ~ "yöneticisi" aynı sözcük sayılıyordu (diyalog seti R11, R14).
    `fuzzy` verilirse iki sözcük de en az 4 harfliyken ekleri atılmış benzerlik bu eşiği geçerse de aynı sayılır."""
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    n = next((i for i, (x, y) in enumerate(zip(short, long_, strict=False)) if x != y), len(short))
    if n >= 4 and (n == len(short)
                   or (n == len(short) - 1 and (short[-1] in _VOWELS or (short[-1], long_[n]) in _SOFTENING))):
        return True
    return (fuzzy is not None and len(a) >= 4 and len(b) >= 4
            and similarity(strip_suffix(a), strip_suffix(b)) >= fuzzy)


def spoken_as(said: str, target: str) -> bool:
    """Cevaptaki `said` sözcüğü `target`ı söylüyor mu: onunla başlıyor ("muhasebedeki") ya da eki atılınca ona
    yakın (STT hatası: "muhasebde")."""
    return said.startswith(target) or similarity(strip_suffix(said), target) >= CHOICE_WORD
