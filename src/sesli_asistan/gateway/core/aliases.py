"""Takma ad ve konu tablosu: akışın 2. adımı (ADR-023, `ALIAS_LOOKUP=mcp`).

Akış: 1. rehber (yerel, katı: ad, soyad, unvan, birim, dahili) → 2. takma ad / konu (müşterinin MCP sunucusu,
`takma_ad_ara`) → 3. LLM. Bu modül 2. adımın aramasıdır; referans MCP sunucusu (`rehber_mcp.py`) ve testler kullanır.
Müşteri kendi sunucusunu yazarsa aynı sözleşmeyi uygular (docs/tasarim.md ADR-023).

Kurallar — bulanık değil, **tam** eşleşme (yanlış kişiye aktarmamak için):
  - Takma ad sözde bütün olarak geçmeli ("Ferhat Bey", "Genel Müdür"); tek kelimelik takma ad yalnız sözün tamamıysa.
  - "Ad + hitap" biçimli takma ad ("Ayşe Hanım", "Mehmet Bey") o ad rehberde birden çok kişide varsa kişi seçmez:
    o addaki herkes döner, Gateway "hangisi?" diye sorar (canlı deneme 03.11.2026: "Ayşe Hanım" iki Ayşe varken
    doğrudan Ayşe Yılmaz'a aktarılmıştı).
  - Konu ("fatura", "arıza", "sipariş") sözde geçerse konunun birimi: birimin genel hattı, yoksa birimdeki kişiler.
    Ekli biçimler de tutar ("arızam", "faturayla"). Takma ad eşleşmesi varsa konu bakılmaz.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .models import Contact
from .search import _HONORIFICS, _TITLES, alias_key, search_key, strip_suffix

MAX_CANDIDATES = 5


@dataclass(frozen=True)
class Topic:
    words: tuple[str, ...]      # "fatura", "fatura itirazı" …
    department: str = ""        # konunun birimi …
    extension: str = ""         # … ya da doğrudan bir dahili

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Topic:
        words = tuple(str(w) for w in (d.get("sozler") or ()) if str(w).strip())
        if not words:
            raise ValueError(f"konunun sözü yok: {d!r}")
        dept, ext = str(d.get("birim") or "").strip(), str(d.get("dahili") or "").strip()
        if bool(dept) == bool(ext):
            raise ValueError(f"konuda birim ya da dahiliden yalnız biri olmalı: {d!r}")
        return cls(words, dept, ext)


class AliasTable:
    def __init__(self, contacts: Iterable[Contact], topics: Iterable[Topic] = (),
                 extra_aliases: dict[str, Iterable[str]] | None = None):
        """`contacts`: aktarılabilir kayıtlar (DirectoryIndex.contacts()); `extra_aliases`: kimlik → ek takma adlar."""
        self.contacts = {c.id: c for c in contacts}
        persons = [c for c in self.contacts.values() if c.kind == "person"]
        self._by_first: dict[str, list[str]] = {}
        for c in persons:
            if k := search_key(c.first_name):
                self._by_first.setdefault(k, []).append(c.id)
        self._aliases: dict[str, set[str]] = {}
        extra = extra_aliases or {}
        for c in self.contacts.values():
            for a in (*c.aliases, *extra.get(c.id, ())):
                if k := alias_key(a):
                    self._aliases.setdefault(k, set()).update(self._owners(k, c))
        self._topics = [(tuple(alias_key(w).split() for w in t.words), t) for t in topics]

    def _owners(self, key: str, owner: Contact) -> set[str]:
        """Takma adın sahipleri: "ad + hitap" ve o ad birden çok kişide → o addaki herkes."""
        words = key.split()
        rest = [w for w in words if w not in _HONORIFICS and w not in _TITLES]
        if owner.kind == "person" and len(rest) == 1 and len(rest) < len(words):
            same = self._by_first.get(search_key(rest[0]), [])
            if owner.id in same and len(same) > 1:
                return set(same)
        return {owner.id}

    def lookup(self, text: str) -> list[dict[str, str]]:
        """Sözdeki takma ad ya da konu → [{"id", "neden": "takma_ad" | "konu", "eslesen"}]; yoksa []."""
        q = alias_key(text)
        if not q:
            return []
        hits: dict[str, str] = {}
        for k, owners in self._aliases.items():
            if k == q or (" " in k and f" {k} " in f" {q} "):
                for cid in owners:
                    hits.setdefault(cid, k)
        if hits:
            ids = sorted(hits, key=lambda cid: self.contacts[cid].full_name)[:MAX_CANDIDATES]
            return [{"id": cid, "neden": "takma_ad", "eslesen": hits[cid]} for cid in ids]
        words = q.split()
        for phrases, topic in self._topics:
            if (matched := next((" ".join(p) for p in phrases if _contains(words, p)), None)) is not None:
                return [{"id": c.id, "neden": "konu", "eslesen": matched}
                        for c in self._topic_targets(topic)[:MAX_CANDIDATES]]
        return []

    def _topic_targets(self, t: Topic) -> list[Contact]:
        if t.extension:
            return [c for c in self.contacts.values() if c.extension == t.extension][:1]
        key = search_key(t.department)
        in_dept = [c for c in self.contacts.values() if search_key(c.department) == key]
        lines = [c for c in in_dept if c.kind == "department"]
        return lines[:1] or sorted(in_dept, key=lambda c: c.full_name)


def _contains(words: list[str], phrase: list[str]) -> bool:
    """`phrase` sözde ardışık geçiyor mu; ekler tolere edilir ("arızam", "faturayla", "siparişimi")."""
    n = len(phrase)
    return any(all(_same(words[i + j], phrase[j]) for j in range(n)) for i in range(len(words) - n + 1))


def _same(word: str, target: str) -> bool:
    return word == target or strip_suffix(word) == target or (len(target) >= 4 and word.startswith(target))


def load_topics(data: Any) -> list[Topic]:
    """{"konular": [{"sozler": [...], "birim": "..."} | {"sozler": [...], "dahili": "..."}]} → konular."""
    items = data.get("konular", []) if isinstance(data, dict) else data
    return [Topic.from_dict(d) for d in items or ()]
