"""Birim yapısıyla seviye seviye daraltma (ADR-032).

Rehber kayıtlarında `unit_path` (birim yolu: kurum → şube → büro) doluysa birden çok aday, yollarının ilk ayrıldığı
seviyeden sorulur: "Büro amiri" → "Jandarma mı, Emniyet mi?" → "Asayiş Şube mi, Trafik Şube mi, Narkotik mi?". Cevap
yalnız o seviyenin adıyla değil, yolun daha aşağısındaki bir adla da olabilir ("cinayet"). Sözcük listeleri ve
kuruma özel kurallar yoktur: ayırt edici sözcükler adayların kendi birim adlarından çıkar.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence

from .models import Contact
from .phrases import question_particle
from .search import fold_words, hard_stem, strip_suffix

MAX_SPOKEN = 3          # bundan çok seçenek "A mı, B mi?" diye sayılmaz, "Hangi birim? A, B, C ya da D."


def _norm(word: str) -> str:
    return hard_stem(strip_suffix(word))


def _words(text: str) -> set[str]:
    return {_norm(w) for w in fold_words(text) if len(w) >= 3}


def unit_names(contacts: Iterable[Contact]) -> dict[tuple[str, ...], set[str]]:
    """Birim yolu → o birimin adları: yoldaki adı + birim hattının (kind=department) birim adı ve eş anlamlıları."""
    out: dict[tuple[str, ...], set[str]] = {}
    for c in contacts:
        for i in range(1, len(c.unit_path) + 1):
            out.setdefault(c.unit_path[:i], set()).add(c.unit_path[i - 1])
        if c.unit_path and c.kind == "department":
            out[c.unit_path].update(n for n in (c.department, *c.department_aliases) if n.strip())
    return out


def split_level(cands: Sequence[Contact]) -> int | None:
    """Adayların birim yollarının ilk ayrıldığı seviye; yolu olmayan aday varsa ya da yollar aynıysa None."""
    if len(cands) < 2 or not all(c.unit_path for c in cands):
        return None
    for i in range(max(len(c.unit_path) for c in cands)):
        if len({c.unit_path[:i + 1] for c in cands}) > 1:
            return i
    return None


def groups(cands: Sequence[Contact], level: int) -> dict[tuple[str, ...], list[Contact]]:
    """Seviyedeki birimler → adayları; sıra rehberdeki dahili sırası (soru her seferinde aynı sırayla sorulur)."""
    out: dict[tuple[str, ...], list[Contact]] = {}
    for c in sorted(cands, key=lambda c: (len(c.extension), c.extension)):
        out.setdefault(c.unit_path[:level + 1], []).append(c)
    return out


def label(prefix: tuple[str, ...], names: dict[tuple[str, ...], set[str]], others: set[str]) -> str:
    """Birimin söylenecek kısa adı: başka seçenekte geçmeyen en kısa ad ("Yeşilova İl Jandarma Komutanlığı" →
    "Jandarma"); yoksa yoldaki adı."""
    official = _words(prefix[-1])

    def rank(n: str) -> tuple[bool, bool, int, int, str]:
        # önce resmî adın içinden olanlar ("Emniyet", "Polis" değil), kısaltma olmayanlar ("Milli Eğitim", "MEB"
        # değil; seslendirmede harf harf okunur), sonra en kısa
        return (not _words(n) <= official, n.isupper(), len(n.split()), len(n), n)

    own = sorted(names.get(prefix, set()) - others, key=rank)
    return own[0] if own else prefix[-1]


def question(level_groups: dict[tuple[str, ...], list[Contact]], names: dict[tuple[str, ...], set[str]]) -> str:
    prefixes = list(level_groups)
    labels = []
    for p in prefixes:
        others: set[str] = set()
        for q in prefixes:
            if q != p:
                others |= names.get(q, set())
        labels.append(label(p, names, others))
    if len(labels) <= MAX_SPOKEN:
        return ", ".join(f"{lab} {question_particle(lab.split()[-1])}" for lab in labels) + "?"
    return f"Hangi birim? {', '.join(labels[:-1])} ya da {labels[-1]}."


def narrow(cands: Sequence[Contact], text: str, names: dict[tuple[str, ...], set[str]]) -> list[Contact]:
    """Cevaptaki birim sözcükleriyle adayları süzer. Ayırt edici sözcük: adayların ortak yolundan sonraki birim
    adlarında geçen ama hepsinde geçmeyen sözcük. Hiçbiri seçilmezse ya da hepsi seçilirse boş liste."""
    if split_level(cands) is None:
        return []
    common = 0
    while all(len(c.unit_path) > common for c in cands) and len({c.unit_path[common] for c in cands}) == 1:
        common += 1
    per: list[set[str]] = []
    for c in cands:
        ws: set[str] = set()
        for i in range(common + 1, len(c.unit_path) + 1):
            for n in names.get(c.unit_path[:i], {c.unit_path[i - 1]}):
                ws |= _words(n)
        per.append(ws)
    distinct = set.union(*per) - set.intersection(*per)
    said = _words(text)

    def like(t: str, w: str) -> bool:
        return t == w or (len(w) >= 4 and t.startswith(w)) or (len(t) >= 4 and w.startswith(t))

    kept = [c for c, ws in zip(cands, per, strict=True) if any(like(t, w) for t in said for w in ws & distinct)]
    return kept if 0 < len(kept) < len(cands) else []
