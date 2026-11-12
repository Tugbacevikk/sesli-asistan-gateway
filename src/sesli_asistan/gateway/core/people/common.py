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


# Arayanın kullandığı unvan → rehberde geçen karşılığı (katlanmış, ön ekle eşlenir: "sekreterine"). Söz
# **genişletilir**, değiştirilmez: "şef" gerçek bir unvansa o da aranmaya devam eder. Burada yalnız genel Türkçe
# karşılıklar; kurumun kendi unvanları kurum dosyasından eklenir (`add_title_synonyms`, ABBREVIATIONS_PATH
# `unvan_es_anlam`), üründe kuruma özgü sözcük tutulmaz (ADR-033).
TITLE_SYNONYMS: dict[str, str] = {"sekreter": "asistan", "mudire": "mudur", "sef": "lider", "patron": "mudur"}


def add_title_synonyms(mapping: dict[str, str]) -> None:
    """Kurumun unvan eş anlamlarını ekler ({"danışman": "uzman"}); anahtar ve değer katlanır, kurumunki genel
    listeyi ezer. Açılışta bir kez çağrılır (composition)."""
    for said, meant in mapping.items():
        k, v = fold_words(said), fold_words(meant)
        if len(k) == 1 and len(v) == 1:
            TITLE_SYNONYMS[k[0]] = v[0]


def _title_words(text: str) -> list[str]:
    """Unvan araması için sözcükler: katlanmış, unvan ve rütbe sözcükleri **atılmaz** ("avukat", "uzmanı",
    "astsubayı" makam hattında asıl sözcüktür; ad aramasında hitap gibi atılırlar); kendini tanıtma ve istek sözleri
    atılır. Eş anlamlı unvan (`title_synonym`) sözcüğün yerinde kabul edilir, listeye eklenmez: unvan araması
    sözcüklerin **sırasına** bakar ("genel müdürün sekreterine" → "genel müdür asistanı")."""
    raw = fold_words(text)
    drop = {raw[i + 1] for i, w in enumerate(raw[:-1]) if w in _SELF}
    return [w for w in raw if w not in drop and w not in _SELF and w not in _SOUND_STOP]


def title_synonym(word: str) -> str:
    """Arayanın kullandığı unvanın rehberdeki karşılığı; eş anlamlı değilse sözcüğün kendisi."""
    return next((v for k, v in TITLE_SYNONYMS.items() if word.startswith(k)), word)


def _cached(index: DirectoryIndex, name: str, build: Any) -> Any:
    """Rehberden türetilen tablo, rehberin kendisinde (`index.derived`, her yeniden kurulmada boşalır): büyük rehberde
    (binlerce makam hattı) her sözde yeniden hesaplanmasın (ölçüm 05.11.2026: 3.500 kayıtla söz başına 0,5–1 sn →
    0,05–0,3 sn)."""
    if name not in index.derived:
        index.derived[name] = build()
    return index.derived[name]


def _bucket(word: str) -> str:
    """`words.same_word` (bulanıklıksız) ancak ilk 4 harfi aynı (ya da 4'ten kısa ve tıpatıp aynı) sözcükler
    arasında doğru olabilir."""
    return word[:4] if len(word) >= 4 else f"={word}"


def _words(text: str) -> set[str]:
    return set(search_key(text).split())


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
