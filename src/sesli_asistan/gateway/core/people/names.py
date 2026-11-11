"""Kişi adı: ad / soyad / hitapla söylenme, tam ad, kesinlik (teyit gerekir mi) ve ses benzerliği."""
from __future__ import annotations

import re
from typing import Any

from rapidfuzz import fuzz

from ..models import Contact
from ..search import (
    DirectoryIndex,
    fold_words,
    is_role_word,
    query_variants,
    search_key,
    similarity,
)
from ..thresholds import NAME_SAME, SOUND_MARGIN, SOUND_MIN
from .common import (
    _HONORIFIC_WORDS,
    _SOUND_STOP,
    _cached,
    _like,
    without_self,
)


def has_honorific(text: str) -> bool:
    return _has_honorific(text)


def _has_honorific(text: str) -> bool:
    """Hitap ya da rütbe, ekli biçimleriyle ("beyi", "hanımla", "albayı")."""
    return any(w.startswith(h) and len(w) - len(h) <= 3 for w in fold_words(text) for h in _HONORIFIC_WORDS)


def _last_name_said(c: Contact, words: list[str]) -> bool:
    k = search_key(c.last_name)
    return bool(k) and any(similarity(v, k) >= NAME_SAME for w in words for v in query_variants(w))


def name_said(c: Contact, words: list[str]) -> bool:
    keys = [k for k in (search_key(c.first_name), search_key(c.last_name)) if k and " " not in k and len(k) >= 3]
    return any(any(similarity(v, k) >= NAME_SAME for v in query_variants(w)) for w in words for k in keys)


def _person_keys(index: DirectoryIndex) -> list[tuple[Contact, str, str]]:
    """(kişi, ad anahtarı, soyad anahtarı) — kaynak rehberdeki tüm kişiler (aktarılamayanlar dahil)."""
    def build() -> list[tuple[Contact, str, str]]:
        return [(c, search_key(c.first_name), search_key(c.last_name))
                for c in index.source_contacts() if c.kind == "person"]
    out: list[tuple[Contact, str, str]] = _cached(index, "person_keys", build)
    return out


def _skeleton(text: str) -> str:
    """Ünsüz iskeleti: ünlüler ve zayıf ünsüzler (ğ, y, h) atılır ("burak celik" → "brkclk")."""
    return re.sub(r"[aeiou ]", "", text.replace("g", "").replace("y", "").replace("h", ""))


def sound_alike(index: DirectoryIndex, text: str) -> list[dict[str, Any]]:
    """Sözdeki 1–2 sözcüklük pencerelerden birine sesçe en çok benzeyen kişi; belirgin değilse []."""
    words = [w for w in search_key(text).split() if w not in _SOUND_STOP]
    wins = [" ".join(words[i:i + n]) for n in (2, 1) for i in range(len(words) - n + 1)]
    wins = [w for w in wins if len(_skeleton(w)) >= 3]
    if not wins:
        return []
    scored = []
    for c in index.contacts():
        if c.kind != "person" or not (name := search_key(c.full_name)):
            continue
        flat, sk = name.replace(" ", ""), _skeleton(name)
        best = max((fuzz.ratio(w.replace(" ", ""), flat) + fuzz.ratio(_skeleton(w), sk)) / 2 for w in wins)
        scored.append((best, c))
    scored.sort(key=lambda x: -x[0])
    if not scored or scored[0][0] < SOUND_MIN:
        return []
    if len(scored) > 1 and scored[0][0] - scored[1][0] < SOUND_MARGIN:
        return []
    return [{"id": scored[0][1].id, "puan": round(scored[0][0], 1)}]


def _full_name_said(c: Contact, words: list[str]) -> bool:
    return _full_name_keys_said(search_key(c.first_name), search_key(c.last_name), words)


def _full_name_keys_said(first: str, last: str, words: list[str]) -> bool:
    if not first or not last or " " in last:
        return False
    hit = [i for i, w in enumerate(words) if any(similarity(v, last) >= NAME_SAME for v in query_variants(w))]
    return any(i > 0 and similarity(words[i - 1], first.split()[-1]) >= NAME_SAME for i in hit)


def said_surely(index: DirectoryIndex, c: Contact, text: str) -> bool:
    """Kural modunda tek bulunan kişi doğrudan aktarılabilir mi (ADR-025 kuralları, LLM'siz akış için): tam ad, ad +
    hitap ("Zeynep Hanım") ya da soyad ("Öztürk Bey", "yılmaz") evet; yalnız ad ("zeynep") ve soyadın önünde başka bir
    ad ("murat çelik" → Burak Çelik) hayır, teyit sorulur. Ölçüm 05.11.2026 (sesli set, arkada ikinci konuşan):
    arka plandan gelen tek bir ad ("zeynep …", "mehmet …") ve bozuk duyulan ad doğrudan aktarılıyordu."""
    words = without_self(text)
    if c.kind != "person" or _full_name_said(c, words):
        return True
    first = search_key(c.first_name).split()
    if _last_name_said(c, words):
        return not _other_given_name(index, c, words)
    if first and any(similarity(v, first[-1]) >= NAME_SAME for w in words for v in query_variants(w)):
        return _has_honorific(text)
    return True                                      # unvan / birimle bulundu: kurallar zaten kesin


def _other_given_name(index: DirectoryIndex, c: Contact, words: list[str]) -> bool:
    """Soyadın hemen önünde kişinin adı olmayan, bağlam sözcüğü de olmayan bir sözcük var mı ("murat çelik")."""
    last, first = search_key(c.last_name), (search_key(c.first_name).split() or [""])[-1]
    depts = [k for x in index.contacts() for name in (x.department, *x.department_aliases)
             for k in search_key(name).split()]
    for i, w in enumerate(words):
        if i == 0 or not any(similarity(v, last) >= NAME_SAME for v in query_variants(w)):
            continue
        prev = words[i - 1]
        if (similarity(prev, first) >= NAME_SAME or prev in _SOUND_STOP or is_role_word(prev)
                or any(_like(prev, k) for k in depts)):
            continue
        return True
    return False
