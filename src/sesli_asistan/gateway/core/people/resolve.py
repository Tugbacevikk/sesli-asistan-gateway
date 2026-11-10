"""Sözden kişi çözme (MCP `kisi_coz` ve Gateway aynı işlevi kullanır) ve sözde geçen hedefler (çoklu hedef denetimi)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..models import Contact
from ..search import (
    DirectoryIndex,
    is_role_word,
    search_key,
)
from .common import (
    MAX_CANDIDATES,
    _title_words,
    _without_self,
)
from .departments import (
    _department_tails,
    _mentioned_departments,
)
from .names import (
    _full_name_keys_said,
    _has_honorific,
    _last_name_said,
    _name_said,
    _other_given_name,
    _person_keys,
    sound_alike,
)
from .titles import (
    _by_title,
)


def resolve_person(index: DirectoryIndex, text: str) -> dict[str, Any]:
    """Söz → {"adaylar": [{"id", "neden": "unvan" | "birim_ad" | "ad"}], "kesin": bool, "ses": [{"id", "puan"}]};
    çözülemezse adaylar boş. "ses": kurallar bir şey bulamadıysa ses benzerliğiyle tek aday (`sound_alike`); Gateway
    onu konu adımından sonra ve **her zaman teyitle** kullanır."""
    out = _rules(index, text)
    out["ses"] = [] if out["adaylar"] else sound_alike(index, text)
    return out


def _rules(index: DirectoryIndex, text: str) -> dict[str, Any]:
    words = _without_self(text)
    if not words:
        return _out([], "", False)
    persons = [c for c in index.contacts() if c.kind == "person"]
    tw = _title_words(text)
    if any(is_role_word(w) for w in tw) and (hit := _by_title(index, tw)):
        return _out(hit, "unvan", len(hit) == 1)
    depts = _mentioned_departments(index, words)
    named = [c for c in persons if _name_said(c, words)]
    if depts and named:
        # birim yolunun üst seviyesi de birimdir: "satıştaki Merve" → Yurt İçi / Yurt Dışı Satış'taki Merve'ler
        # (canlı deneme 10.11.2026, şirket rehberi: yalnız kendi birimine bakılıyordu, altı Merve soruldu)
        in_dept = [c for c in named if c.department in depts or set(c.unit_path) & depts]
        if in_dept:
            return _out(in_dept, "birim_ad", len(in_dept) == 1)
        # söylenen birimde o ad yok ("satıştan Burak Bey"; Burak muhasebede): aktarılmaz, teyit sorulur
        return _out(named, "ad", False)
    if len(named) > MAX_CANDIDATES and all(c.unit_path for c in named):
        # adaşı çok ("Volkan Bey", dört Volkan) ama birim yolu var: hepsi döner, Gateway birim yoluyla sorar (ADR-032).
        # Kesilince dördüncü aday hiç sorulmuyordu (canlı deneme 10.11.2026)
        return _out(named, "ad", False, limit=_UNIT_PATH_MAX)
    if named and len(named) <= MAX_CANDIDATES:
        # hitapla ("Zeynep Hanım", "Can Bey", "Mehmet Albay") ya da soyadla ("Öztürk Bey") söylenen tek kişi: kesin
        # (kural modu ve LLM de doğrudan aktarıyordu); yalnız ad ("Mehmet var mı"): teyit
        sure = (len(named) == 1 and (_has_honorific(text) or _last_name_said(named[0], words))
                and not _other_given_name(index, named[0], words))       # "murat çelik" ≠ Burak Çelik: teyit
        return _out(named, "ad", sure)
    return _out([], "", False)


_UNIT_PATH_MAX = 30          # birim yoluyla sorulacak adaşlarda üst sınır (çok büyük rehberde soru anlamsızlaşır)


def _out(cands: list[Contact], why: str, sure: bool, limit: int = MAX_CANDIDATES) -> dict[str, Any]:
    cands = sorted(cands, key=lambda c: c.full_name)[:limit]
    return {"adaylar": [{"id": c.id, "neden": why} for c in cands], "kesin": sure and len(cands) == 1}


@dataclass(frozen=True)
class Mentions:
    """Sözde geçen hedefler: kişiler (tam adla), birimler, dahililer (sahibi), aktarılamayan kişiler, operatör /
    kapatma isteği. Kişinin kendi birimi ya da kendi dahilisi ayrı hedef sayılmaz ("muhasebeden Ayşe Yılmaz")."""

    contacts: tuple[Contact, ...]       # aktarılabilir hedefler (kişi, birim hattı, dahili sahibi)
    departments: tuple[str, ...]        # hattı olmayan, kişisi de söylenmeyen birimler
    others: tuple[Contact, ...]         # rehberde olup aktarılamayan kişiler (yönetim vb.)
    surnames: tuple[Contact, ...] = ()  # yalnız soyadı geçen kişiler (operatör / kapatma isteği yanında hedef sayılır)

    @property
    def count(self) -> int:
        return len(self.contacts) + len(self.departments) + len(self.others)


def mentioned_targets(index: DirectoryIndex, text: str, numbers: list[str]) -> Mentions:
    """`numbers`: sözde geçen sayılar (dahili olabilir; çağıran çıkarır)."""
    words = _without_self(text)
    if not words:
        return Mentions((), (), ())
    keys = _person_keys(index)
    persons = [c for c, _, _ in keys]
    named = [c for c, first, last in keys if _full_name_keys_said(first, last, words)]
    by_id: dict[str, Contact] = {c.id: c for c in named if c.transferable}
    # aktarılamayan kişi (yönetim) adıyla ya da soyadıyla da sayılır ("hakan bey"): azdır, karışma riski düşük
    others = tuple(c for c in persons if not c.transferable and (c in named or _name_said(c, words)))
    for n in numbers:
        owner = next((c for c in index.contacts() if c.extension == n), None)
        if owner is not None and not any(c.id == owner.id for c in named):
            by_id.setdefault(owner.id, owner)
    said_depts = _mentioned_departments(index, words) | _department_tails(index, words)
    covered = {c.department for c in by_id.values()} | {c.department for c in others}
    rest: list[str] = []
    for d in sorted(said_depts - covered):
        line = next((c for c in index.contacts() if c.kind == "department" and c.department == d), None)
        if line is not None:
            by_id.setdefault(line.id, line)
        else:
            rest.append(d)
    surnames = tuple(c for c in persons if c.transferable and c.id not in by_id and len(search_key(c.last_name)) >= 4
                     and _last_name_said(c, words))
    return Mentions(tuple(sorted(by_id.values(), key=lambda c: c.full_name or c.department)), tuple(rest), others,
                    surnames)
