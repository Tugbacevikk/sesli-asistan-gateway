"""Kişi çözme: akışın 2. adımı (ADR-025, MCP `kisi_coz`).

Akış: 1. rehber (hızlı yol: tam ad, birim hattı, dahili, "hangisi?") → 2. MCP (`kisi_coz`, `konu_ara`) → 3. LLM.
1. adım yalnız kesin durumlarda karar verir; şu üç durumda kenara çekilir ve söz eskiden LLM'e giderdi (3–6 sn).
Rehberdeki bilgi yeter, kurallar burada:

  1. Unvan ("İl Jandarma Komutanı", "muhasebe müdürü", "vali yardımcısı"): sözde unvan sözcüğü var ve unvanla tek
     kişi bulunuyor → o kişi (kesin). 2–3 kişi → hangisi sorulur.
  2. Birim + ad ("muhasebeden Ayşe Hanım"): söylenen birimde o ad / soyadla tek kişi → o kişi (kesin).
  3. Ad + hitap ya da yalnız ad ("Mehmet Bey"): rehberde o ad / soyadla tek kişi → o kişi, **kesin değil** (arayana
     teyit sorulur: kişinin kendisi mi, aynı adlı rehberde olmayan biri mi bilinmez). Birden çok → hangisi.

Arayanın kendini tanıttığı ad ("ben Mehmet, …", "adım Ali") aday sayılmaz. Gateway ve referans MCP sunucusu aynı
işlevi kullanır: MCP düşerse Gateway aynısını kendisi çalıştırır (LLM'e düşmez).
"""
from __future__ import annotations

from typing import Any

from .models import Contact, Match
from .search import (
    _HONORIFICS,
    _RANKS,
    DirectoryIndex,
    fold_words,
    has_role_word,
    is_role_word,
    query_variants,
    search_key,
    similarity,
)

MAX_CANDIDATES = 3
_SELF = {"ben", "adim", "ismim", "benim"}
_HONORIFIC_WORDS = _HONORIFICS | _RANKS


def resolve_person(index: DirectoryIndex, text: str) -> dict[str, Any]:
    """Söz → {"adaylar": [{"id", "neden": "unvan" | "birim_ad" | "ad"}], "kesin": bool}; çözülemezse adaylar boş."""
    words = _without_self(text)
    if not words:
        return _out([], "", False)
    persons = [c for c in index.contacts() if c.kind == "person"]
    if has_role_word(text) and (hit := _by_title(index, words)):
        return _out(hit, "unvan", len(hit) == 1)
    depts = _mentioned_departments(index, words)
    named = [c for c in persons if _name_said(c, words)]
    if depts and named:
        in_dept = [c for c in named if c.department in depts]
        if in_dept:
            return _out(in_dept, "birim_ad", len(in_dept) == 1)
        # söylenen birimde o ad yok ("satıştan Burak Bey"; Burak muhasebede): aktarılmaz, teyit sorulur
        return _out(named, "ad", False)
    if named and len(named) <= MAX_CANDIDATES:
        # hitapla ("Zeynep Hanım", "Can Bey", "Mehmet Albay") ya da soyadla ("Öztürk Bey") söylenen tek kişi: kesin
        # (kural modu ve LLM de doğrudan aktarıyordu); yalnız ad ("Mehmet var mı"): teyit
        sure = len(named) == 1 and (_has_honorific(text) or _last_name_said(named[0], words))
        return _out(named, "ad", sure)
    return _out([], "", False)


def _has_honorific(text: str) -> bool:
    """Hitap ya da rütbe, ekli biçimleriyle ("beyi", "hanımla", "albayı")."""
    return any(w.startswith(h) and len(w) - len(h) <= 3 for w in fold_words(text) for h in _HONORIFIC_WORDS)


def _last_name_said(c: Contact, words: list[str]) -> bool:
    k = search_key(c.last_name)
    return bool(k) and any(similarity(v, k) >= 92 for w in words for v in query_variants(w))


def _out(cands: list[Contact], why: str, sure: bool) -> dict[str, Any]:
    cands = sorted(cands, key=lambda c: c.full_name)[:MAX_CANDIDATES]
    return {"adaylar": [{"id": c.id, "neden": why} for c in cands], "kesin": sure and len(cands) == 1}


def _without_self(text: str) -> list[str]:
    """Arama sözcükleri; kendini tanıtma ("ben mehmet", "adım ali") ardındaki sözcük atılır."""
    raw = fold_words(text)
    drop = {raw[i + 1] for i, w in enumerate(raw[:-1]) if w in _SELF}
    return [w for w in search_key(" ".join(w for w in raw if w not in drop)).split()]


def _name_said(c: Contact, words: list[str]) -> bool:
    keys = [k for k in (search_key(c.first_name), search_key(c.last_name)) if k and " " not in k and len(k) >= 3]
    return any(any(similarity(v, k) >= 92 for v in query_variants(w)) for w in words for k in keys)


def _mentioned_departments(index: DirectoryIndex, words: list[str]) -> set[str]:
    """Sözde geçen birimler (adı ya da eş anlamlısı ardışık sözcüklerle; ekli biçimler: "bilgi işlemden")."""
    out: set[str] = set()
    for c in index.contacts():
        for name in (c.department, *c.department_aliases):
            k = search_key(name).split()
            if k and any(all(_like(words[i + j], k[j]) for j in range(len(k)))
                         for i in range(len(words) - len(k) + 1)):
                out.add(c.department)
    return out


def _by_title(index: DirectoryIndex, words: list[str]) -> list[Contact]:
    """Önce sözcük kapsaması (`_by_coverage`), olmazsa unvan sözcüğünü içeren en uzun pencereden (4 → 2 sözcük)
    bulanık unvan araması."""
    if hit := _by_coverage(index, words):
        return hit
    for n in (4, 3, 2):
        found: dict[str, Contact] = {}
        for i in range(len(words) - n + 1):
            window = words[i:i + n]
            if not has_role_word(" ".join(window)):
                continue
            res = index.search(name=" ".join(window))
            if res.match in (Match.SINGLE_HIGH, Match.MULTIPLE) and not res.truncated:
                for cand in res.candidates:
                    c = cand.contact
                    if c.kind == "person" and c.title and cand.score >= index.high_score:
                        found[c.id] = c
        if found:
            return list(found.values())
    return []


def _words(text: str) -> set[str]:
    return set(search_key(text).split())


def _like(a: str, b: str) -> bool:
    """Aynı sözcüğün ekli biçimleri: biri ötekinin başı ("komutanıyla" ~ "komutanı", "kaymakamı" ~ "kaymakamlığı").
    Türkçe ek atmak yerine ortak baş: katmanlı eklerde ("yardımcısına") ek atıcı kökü tutarlı bulamıyor."""
    if a == b:
        return True
    n = next((i for i, (x, y) in enumerate(zip(a, b, strict=False)) if x != y), min(len(a), len(b)))
    return n >= 4 and n >= min(len(a), len(b)) - 1


def _by_coverage(index: DirectoryIndex, words: list[str]) -> list[Contact]:
    """Unvan sözcüğü ve önündeki en çok 3 sözcüğün **hepsi** kişinin unvanında ya da biriminde geçiyor ("jandarma
    komutanıyla" → İl Jandarma Komutanı; "Nilüfer kaymakamı" → Nilüfer Kaymakamlığı'ndaki Kaymakam). Birden çok kişi
    uyarsa unvanında sözde geçmeyen sözcüğü en az olan(lar) ("komutan yardımcısı" değil "komutan"). Tek sözcükte
    ("kaymakam") yalnız unvanın tamamı o sözcükse."""
    people = [(c, _words(c.title), _words(c.title) | _words(c.department))
              for c in index.contacts() if c.kind == "person" and c.title]
    for i in reversed(range(len(words))):        # Türkçede unvanın başı sonda: "komutan yardımcısı" → yardımcı
        if not is_role_word(words[i]):
            continue
        for n in (4, 3, 2, 1):
            if i - n + 1 < 0:
                continue
            said = words[i - n + 1:i + 1]
            # (unvanında geçen söz sayısı, unvanında sözde geçmeyen sözcük sayısı): "emniyet müdürü" → İl Emniyet
            # Müdürü (emniyet unvanında), Trafik Şube Müdürü değil (emniyet yalnız biriminde)
            hits = [(sum(any(_like(s, x) for x in title) for s in said),
                     sum(not any(_like(s, x) for s in said) for x in title), c)
                    for c, title, vocab in people if all(any(_like(s, v) for v in vocab) for s in said)]
            if n == 1:
                hits = [h for h in hits if h[1] == 0]
            if hits:
                best = max((inside, -extra) for inside, extra, _ in hits)
                return [c for inside, extra, c in hits if (inside, -extra) == best]
    return []
