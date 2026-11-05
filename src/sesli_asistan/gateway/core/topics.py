"""Konu → birim: akışın 2. adımı (ADR-023).

Akış: 1. rehber (yerel: ad, soyad, unvan, birim, dahili) → 2. MCP `konu_ara` (bu modül, yerel MCP sunucusunda) →
3. LLM. Arayan kişi ya da birim değil **konu** söylediğinde ("faturamla ilgili", "arızam var", "sipariş vereceğim")
LLM'e gitmeden konunun birimine aktarılır.

Konu listesi şirkete özel değildir: birimler **ad kalıbıyla** yazılır ("muhasebe", "finans", "mali işler") ve
konuşma sırasında müşterinin rehberindeki gerçek birime eşlenir ("Muhasebe Müdürlüğü", "Mali İşler"). Rehberde
karşılığı yoksa ya da kalıp birden çok birime uyuyorsa adım bir şey döndürmez: söz LLM'e gider (yanlış birime
aktarılmaz). Ürünle gelen genel liste: `gateway/konular.json`; kurum kendi listesini `MCP_TOPICS_PATH` ile verir.

Eşleşme bulanık değil, söz geçmeli; ekler tolere edilir ("arızam", "faturamla", "siparişimi"). Birden çok konu
geçerse en uzun (en belirli) söz kazanır: "su arızası" → su işleri, "arıza" → teknik destek.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .models import Contact
from .search import fold_words, search_key, strip_suffix
from .thresholds import MAX_TOPIC_CANDIDATES

MAX_CANDIDATES = MAX_TOPIC_CANDIDATES


@dataclass(frozen=True)
class Topic:
    words: tuple[str, ...]                 # "fatura", "fiyat teklifi" …
    departments: tuple[str, ...] = ()      # birim ad kalıpları, öncelik sırasıyla …
    extension: str = ""                    # … ya da doğrudan bir dahili (kuruma özel listede)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Topic:
        words = tuple(str(w).strip() for w in (d.get("sozler") or ()) if str(w).strip())
        depts = tuple(str(b).strip() for b in (d.get("birimler") or ()) if str(b).strip())
        ext = str(d.get("dahili") or "").strip()
        if not words:
            raise ValueError(f"konunun sözü yok: {d!r}")
        if bool(depts) == bool(ext):
            raise ValueError(f"konuda birimler ya da dahiliden yalnız biri olmalı: {d!r}")
        return cls(words, depts, ext)


def parse_topics(data: Any) -> list[Topic]:
    """{"konular": [{"sozler": [...], "birimler": [...]} | {"sozler": [...], "dahili": "..."}]} → konular."""
    items = data.get("konular", []) if isinstance(data, dict) else data
    return [Topic.from_dict(d) for d in items or ()]


class TopicTable:
    def __init__(self, contacts: Iterable[Contact], topics: Iterable[Topic]):
        """`contacts`: aranabilir (aktarılabilir) kayıtlar, DirectoryIndex.contacts()."""
        self.contacts = list(contacts)
        self._depts: dict[str, list[list[str]]] = {}            # birim adı → [ad ve eş anlamlılarının sözcükleri]
        for c in self.contacts:
            if c.department:
                keys = self._depts.setdefault(c.department, [])
                for name in (c.department, *c.department_aliases):
                    if (k := search_key(name).split()) and k not in keys:
                        keys.append(k)
        self.topics = [(sorted((fold_words(w) for w in t.words), key=len, reverse=True), t) for t in topics]

    def lookup(self, text: str) -> list[dict[str, str]]:
        """Sözdeki konu → [{"id", "neden": "konu", "eslesen", "birim"}]; konu yok ya da birim çözülemedi → []."""
        words = fold_words(text)
        if not words:
            return []
        best: tuple[int, int, str, Topic] | None = None
        for order, (phrases, topic) in enumerate(self.topics):
            for p in phrases:
                if _contains(words, p) and (best is None or (len(p), -order) > (best[0], -best[1])):
                    best = (len(p), order, " ".join(p), topic)
                    break                              # konunun en uzun sözü (sözler uzundan kısaya)
        if best is None:
            return []
        _, _, matched, topic = best
        dept, targets = self._targets(topic)
        return [{"id": c.id, "neden": "konu", "eslesen": matched, "birim": dept} for c in targets[:MAX_CANDIDATES]]

    def _targets(self, t: Topic) -> tuple[str, list[Contact]]:
        if t.extension:
            hit = [c for c in self.contacts if c.extension == t.extension][:1]
            return (hit[0].department if hit else ""), hit
        for pattern in t.departments:
            p = search_key(pattern).split()
            found = [d for d, keys in self._depts.items() if any(_contains(k, p) for k in keys)]
            if len(found) > 1:
                return "", []            # kalıp birden çok birime uyuyor: hangisi olduğu belirsiz → LLM
            if found:
                in_dept = [c for c in self.contacts if c.department == found[0]]
                lines = [c for c in in_dept if c.kind == "department"]
                return found[0], lines[:1] or sorted(in_dept, key=lambda c: c.full_name)
        return "", []


def _contains(words: list[str], phrase: list[str]) -> bool:
    """`phrase` sözde ardışık geçiyor mu; ekler tolere edilir."""
    n = len(phrase)
    return n > 0 and any(all(_same(words[i + j], phrase[j]) for j in range(n)) for i in range(len(words) - n + 1))


def _same(word: str, target: str) -> bool:
    return word == target or strip_suffix(word) == target or (len(target) >= 4 and word.startswith(target))
