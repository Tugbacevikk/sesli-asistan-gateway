"""Unvan ve makam hatları (ADR-025, ADR-030): unvanla kişi / makam bulma, aynı unvanlı hatlar,
birim / şube sözcükleriyle daraltma."""
from __future__ import annotations

from ..models import Contact, Match
from ..search import (
    DirectoryIndex,
    fold_words,
    has_role_word,
    is_role_word,
    similarity,
    strip_suffix,
)
from ..thresholds import CHOICE_WORD
from .common import (
    _bucket,
    _cached,
    _like,
    _title_words,
)


def _title_table(index: DirectoryIndex) -> list[tuple[Contact, set[str], set[str]]]:
    """(kayıt, unvan sözcükleri, unvan + birim sözcükleri); rehber sürümüne göre önbellekte (büyük rehberde her sözde
    yeniden kurulmasın)."""
    def build() -> list[tuple[Contact, set[str], set[str]]]:
        table = []
        for c in index.contacts():
            if c.kind != "department" and c.title:
                title = set(fold_words(c.title))
                table.append((c, title, title | set(fold_words(c.department))))
        return table
    out: list[tuple[Contact, set[str], set[str]]] = _cached(index, "title_table", build)
    return out


def _title_buckets(index: DirectoryIndex) -> dict[str, set[int]]:
    """Unvan + birim sözcüğünün kovası → `_title_table` satırları: kapsama araması yalnız olası satırlara bakar."""
    def build() -> dict[str, set[int]]:
        out: dict[str, set[int]] = {}
        for r, (_, _, vocab) in enumerate(_title_table(index)):
            for v in vocab:
                out.setdefault(_bucket(v), set()).add(r)
                if len(v) == 4:                        # 4 harfli sözcük: hem kova hem tam
                    out.setdefault(f"={v}", set()).add(r)
        return out
    result: dict[str, set[int]] = _cached(index, "title_buckets", build)
    return result


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
                    if c.kind != "department" and c.title and cand.score >= index.high_score:
                        found[c.id] = c
        if found:
            return list(found.values())
    return []


def _drop_sublines(cands: list[Contact]) -> list[Contact]:
    """Aynı birimde bir makamın alt/cihaz hatları ("… Müdürü Shp.", "… Dinlenme", "… Mbl.") varsa ve asıl makamın
    sözcük kümesi onun öz alt kümesiyse yalnız asıl makam kalır."""
    if len(cands) <= 1:
        return cands
    sets = [(c, set(fold_words(c.title))) for c in cands]
    return [c for c, w in sets
            if not any(o.department == c.department and ow < w for o, ow in sets if o.id != c.id)]


def same_title(index: DirectoryIndex, text: str) -> list[Contact]:
    """Sözdeki unvana ("müfettiş", "teftiş kurulu müfettişi") uyan **tüm** kişi ve makam hatları (en iyisi değil):
    aynı unvanlı çok hatta birimle süzmek için (ADR-030). Sözü en çok kapsayanlar; içlerinde unvanı tam söylenen
    tek kayıt varsa ("personel daire başkanı" → Personel Daire Başkanı, o başkanlığın şube müdürleri değil) boş:
    hedef belli."""
    hits = _coverage_hits(index, _title_words(text), exact_single=False)
    if not hits:
        return []
    top = max(inside for inside, _, _ in hits)
    tier = [(extra, c) for inside, extra, c in hits if inside == top]
    if sum(extra == 0 for extra, _ in tier) == 1:
        return []
    cleaned = _drop_sublines([c for _, c in tier])
    return [] if len(cleaned) <= 1 else cleaned


def _by_coverage(index: DirectoryIndex, words: list[str]) -> list[Contact]:
    """Unvan sözcüğü ve önündeki sözcüklerin **hepsi** kişinin unvanında ya da biriminde geçiyor ("jandarma
    komutanıyla" → İl Jandarma Komutanı; "Nilüfer kaymakamı" → Nilüfer Kaymakamlığı'ndaki Kaymakam). Birden çok kişi
    uyarsa unvanında sözde geçmeyen sözcüğü en az olan(lar) ("komutan yardımcısı" değil "komutan"). Tek sözcükte
    ("kaymakam") yalnız unvanın tamamı o sözcükse."""
    hits = _coverage_hits(index, words)
    if not hits:
        return []
    best = max((inside, -extra) for inside, extra, _ in hits)
    return [c for inside, extra, c in hits if (inside, -extra) == best]


def _coverage_hits(index: DirectoryIndex, words: list[str], exact_single: bool = True
                   ) -> list[tuple[int, int, Contact]]:
    """(unvanında geçen söz sayısı, unvanında sözde geçmeyen sözcük sayısı, kayıt): unvan sözcüğüyle biten en uzun
    pencere (8 → 1 sözcük) için. `exact_single`: tek sözcükte yalnız unvanı tam o sözcük olanlar."""
    people = _title_table(index)                       # kişi ve makam hattı
    buckets = _title_buckets(index)
    for i in reversed(range(len(words))):        # Türkçede unvanın başı sonda: "komutan yardımcısı" → yardımcı
        if not is_role_word(words[i]):
            continue
        for n in range(min(8, i + 1), 0, -1):
            said = words[i - n + 1:i + 1]
            # (unvanında geçen söz sayısı, unvanında sözde geçmeyen sözcük sayısı): "emniyet müdürü" → İl Emniyet
            # Müdürü (emniyet unvanında), Trafik Şube Müdürü değil (emniyet yalnız biriminde)
            rows = set.intersection(*(buckets.get(_bucket(s), set()) for s in said))   # olası kayıtlar
            raw = [(c, title, vocab) for c, title, vocab in (people[r] for r in sorted(rows))
                   if all(any(_like(s, v) for v in vocab) for s in said)]
            if not raw:
                continue
            prefix = [w for w in words[:i - n + 1] if len(w) >= 3]
            used = said
            if prefix and len(raw) > 1 and n >= 2:
                ps = [sum(any(_like(p, v) for v in vocab) for p in prefix) for _, _, vocab in raw]
                if (best_p := max(ps)) > 0:
                    raw = [r for r, p in zip(raw, ps, strict=True) if p == best_p]
                    used = [*prefix, *said]
            hits = [(sum(any(_like(s, x) for x in title) for s in used),
                     sum(not any(_like(s, x) for s in used) for x in title), c)
                    for c, title, _ in raw]
            if len(used) == 1 and exact_single:
                hits = [h for h in hits if h[1] == 0]
            if hits:
                return hits
    return []


_UNIT_WORDS = {"baskanligi", "bakanligi", "komutanligi", "mudurlugu", "daire", "dairesi", "sube", "subesi",
               "kurulu", "merkezi", "grup", "kisim", "buro", "servisi", "tesis", "tesisi", "makami"}


_NARROW_STOP = {"birim", "birimi", "birimdeki", "birimine", "subedeki", "dairedeki", "baskanligindaki", "bolum",
                "departman", "hatti", "hattina", "baglanmak", "ulasmak"}


def _word_matches(w: str, x: str) -> bool:
    if _like(w, x) or (len(x) >= 4 and w.startswith(x)):
        return True
    return len(w) >= 4 and len(x) >= 4 and similarity(strip_suffix(w), strip_suffix(x)) >= CHOICE_WORD


def _is_unit_word(w: str) -> bool:
    return not is_role_word(w) and any(_like(w, u) for u in _UNIT_WORDS)


def _unit_prefix_words(c: Contact) -> set[str]:
    fw = fold_words(c.title)
    last_u = max((i for i, w in enumerate(fw) if _is_unit_word(w)), default=-1)
    return set(fw[:last_u + 1]) if last_u >= 0 else set()


def narrow_candidates(cands: list[Contact] | tuple[Contact, ...], text: str) -> list[Contact]:
    """Aday kümesini (`cands`) söylenen birim / şube / unvan sözcükleriyle daraltır (çok turlu filtreleme)."""
    words = [w for w in _title_words(text) if len(w) >= 3 and w not in _NARROW_STOP]
    if not words or not cands:
        return []
    vocab = [set(fold_words(f"{c.department} {c.title}")) for c in cands]
    common = set.intersection(*vocab) if vocab else set()
    dist_words = [w for w in words if w not in common] or words
    scored: list[tuple[int, Contact]] = []
    for c, v in zip(cands, vocab, strict=True):
        dist = {x for x in v - common if len(x) >= 3}
        m = sum(1 for w in dist_words if any(_word_matches(w, x) for x in dist))
        if m > 0:
            scored.append((m, c))
    if not scored:
        return []
    best_m = max(m for m, _ in scored)
    top = [c for m, c in scored if m == best_m]
    has_role = any(is_role_word(w) for w in words) or any(is_role_word(w) for w in common)
    if not has_role:
        dept_only = [c for c in top
                     if all(any(_word_matches(w, d) for d in fold_words(c.department))
                            for w in words
                            if any(_word_matches(w, x) for x in fold_words(f"{c.department} {c.title}")))]
        if dept_only:
            return dept_only
        rem_u = []
        for c in top:
            dept_w = set(fold_words(c.department)) | {"baskanligi", "bakanligi", "komutanligi", "mudurlugu", "sube",
                                                      "subesi"}
            u_dist = {x for x in _unit_prefix_words(c) - common - dept_w if len(x) >= 3}
            unm = sum(1 for x in u_dist if not any(_word_matches(w, x) for w in words))
            mat = sum(1 for x in u_dist if any(_word_matches(w, x) for w in words))
            rem_u.append((mat > 0, unm, c))
        if any(has_mat for has_mat, _, _ in rem_u):
            min_unm = min(unm for has_mat, unm, _ in rem_u if has_mat)
            return [c for has_mat, unm, c in rem_u if has_mat and unm == min_unm]
        return top
    top = _drop_sublines(top)
    rem_r = []
    for c in top:
        dept_w = set(fold_words(c.department)) | _UNIT_WORDS
        t_dist = {x for x in set(fold_words(c.title)) - common - dept_w if len(x) >= 3}
        unm = sum(1 for x in t_dist if not any(_word_matches(w, x) for w in words))
        mat = sum(1 for x in t_dist if any(_word_matches(w, x) for w in words))
        rem_r.append((mat > 0, unm, c))
    exact = [c for has_mat, unm, c in rem_r if has_mat and unm == 0]
    return exact or top


def same_unit(index: DirectoryIndex, text: str) -> list[Contact]:
    """Arayan henüz unvan söylemeden bir başkanlık / daire / şube / müdürlük söylediğinde ("asayiş başkanlığı",
    "plan koordinasyon şube", "jandarma sosyal tesis müdürlüğü") o birimdeki makam hatlarını döner; alt hatlar
    elendikten sonra tek makam kalırsa doğrudan o makama yönlendirilir, birden çoksa sonraki turda daraltılır."""
    words = [w for w in _title_words(text) if len(w) >= 3 and w not in _NARROW_STOP]
    specific = [w for w in words if not _is_unit_word(w)]
    has_unit = any(_is_unit_word(w) for w in words)
    if not specific or (len(specific) < 2 and not has_unit):
        return []
    roles = [c for c in index.contacts() if c.kind == "role" and c.transferable]
    if not roles:
        return []
    buckets = _title_buckets(index)
    if not all(buckets.get(_bucket(w)) for w in specific):
        return []
    cands = [c for c in narrow_candidates(roles, text)
             if all(any(_word_matches(w, x) for x in fold_words(f"{c.department} {c.title}")) for w in specific)]
    cleaned = _drop_sublines(cands)
    if len(cleaned) == 1 and has_unit:
        return cleaned
    return cands if len(cands) >= 2 else []
