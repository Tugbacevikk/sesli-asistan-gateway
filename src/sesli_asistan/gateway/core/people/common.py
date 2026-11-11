"""Kişi çözmenin ortak parçaları: sözcük kümeleri, kendini tanıtmanın atılması, ekli biçim
benzerliği, rehberden türetilen tabloların önbelleği."""
from __future__ import annotations

from typing import Any

from ..search import (
    HONORIFICS,
    RANKS,
    DirectoryIndex,
    fold_words,
    search_key,
)
from ..thresholds import MAX_PERSON_CANDIDATES

MAX_CANDIDATES = MAX_PERSON_CANDIDATES


_SELF = {"ben", "adim", "ismim", "benim"}


_HONORIFIC_WORDS = HONORIFICS | RANKS


def without_self(text: str) -> list[str]:
    """Arama sözcükleri; kendini tanıtma ("ben mehmet", "adım ali") ardındaki sözcük atılır."""
    raw = fold_words(text)
    drop = {raw[i + 1] for i, w in enumerate(raw[:-1]) if w in _SELF}
    return list(search_key(" ".join(w for w in raw if w not in drop)).split())


def _title_words(text: str) -> list[str]:
    """Unvan araması için sözcükler: katlanmış, unvan ve rütbe sözcükleri **atılmaz** ("avukat", "uzmanı",
    "astsubayı" makam hattında asıl sözcüktür; ad aramasında hitap gibi atılırlar); kendini tanıtma ve istek sözleri
    atılır."""
    raw = fold_words(text)
    drop = {raw[i + 1] for i, w in enumerate(raw[:-1]) if w in _SELF}
    return [w for w in raw if w not in drop and w not in _SELF and w not in _SOUND_STOP]


def _cached(index: DirectoryIndex, name: str, build: Any) -> Any:
    """Rehberden türetilen tablo, rehberin kendisinde (`index.derived`, her yeniden kurulmada boşalır): büyük rehberde
    (binlerce makam hattı) her sözde yeniden hesaplanmasın (ölçüm 05.11.2026: 3.500 kayıtla söz başına 0,5–1 sn →
    0,05–0,3 sn)."""
    if name not in index.derived:
        index.derived[name] = build()
    return index.derived[name]


def _bucket(word: str) -> str:
    """`_like` ancak ilk 4 harfi aynı (ya da 4'ten kısa ve tıpatıp aynı) sözcükler arasında doğru olabilir."""
    return word[:4] if len(word) >= 4 else f"={word}"


def _words(text: str) -> set[str]:
    return set(search_key(text).split())


_SOFTENING = {("p", "b"), ("t", "d"), ("k", "g")}    # katlanmış: kitap/kitabı, kanat/kanadı, renk/rengi
_VOWELS = set("aeiou")                                              # katlanmış ünlüler (ı→i, ö→o, ü→u)


def _like(a: str, b: str) -> bool:
    """Aynı sözcüğün ekli biçimleri: biri ötekinin başı ("komutanıyla" ~ "komutanı", "kaymakamı" ~ "kaymakamlığı").
    Türkçe ek atmak yerine ortak baş: katmanlı eklerde ("yardımcısına") ek atıcı kökü tutarlı bulamıyor. Kısa sözcüğün
    son harfi ayrışabilir, ama yalnız ek ünlüsü ("kaymakamı") ya da ünsüz yumuşaması ("kitap" ~ "kitabı") ise: şirket
    rehberinde "satış" ~ "satınalma", "yönetim" ~ "yöneticisi" aynı sözcük sayılıyordu (diyalog seti R11, R14)."""
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    n = next((i for i, (x, y) in enumerate(zip(short, long_, strict=False)) if x != y), len(short))
    if n < 4:
        return False
    if n == len(short):
        return True
    return n == len(short) - 1 and (short[-1] in _VOWELS or (short[-1], long_[n]) in _SOFTENING)


# --- ses benzerliği (STT adı bozduğunda) ---------------------------------------------------------------------------
# Ölçüm 04.11.2026 (mihu, telefon): "Ayşe Yılmaz" → "ağlayamaz", "Burak Çelik" → "bırakçayı", "Mehmet Demir'e" →
# "mehmet de mi'ye". Yazım benzerliği ile ünsüz iskeleti benzerliğinin ortalaması; en iyi aday SOUND_MIN'i geçmeli ve
# ikinciden en az SOUND_MARGIN önde olmalı. Gerçek STT hatalarından 6/8'i doğru, ad içermeyen 29 sözün hiçbiri
# tetiklemedi. Eşikler: thresholds.SOUND_MIN / SOUND_MARGIN. Yalnız teyitle: yanlış eşleşmenin bedeli tek soru.
# eşleşmeye girmeyen sık sözcükler (katlanmış): istek fiilleri, soru ekleri
_SOUND_STOP = {"istiyorum", "istiyordum", "ariyorum", "aliyorum", "gorusmek", "gorusecektim", "musunuz", "misiniz",
               "misin", "musun", "ile", "bir", "lutfen", "baglar", "baglayin", "baglayabilir", "var", "mi", "mu",
               "icin", "beraber", "verir", "acaba", "rica", "ederim", "orada", "burada", "kim", "kimle", "kiminle",
               "istiyor", "istiyoruz", "isterim", "isteriz", "istemistim", "ariyor", "gorusebilir", "konusmak",
               "konusacaktim", "baglanmak", "baglanabilir", "ulasmak", "ulasabilir", "miyim", "muyum", "miyiz", "muyuz",
               "aktar", "aktarin", "aktarabilir", "merhaba", "iyi", "gunler", "selam", "alo"}
