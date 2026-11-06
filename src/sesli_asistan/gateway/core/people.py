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

import re
from dataclasses import dataclass
from typing import Any

from rapidfuzz import fuzz

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
    strip_suffix,
)
from .thresholds import CHOICE_WORD, FUZZY_TARGET_MIN, MAX_PERSON_CANDIDATES, NAME_SAME, SOUND_MARGIN, SOUND_MIN
from .topics import TopicTable

MAX_CANDIDATES = MAX_PERSON_CANDIDATES
_SELF = {"ben", "adim", "ismim", "benim"}
_HONORIFIC_WORDS = _HONORIFICS | _RANKS


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
        in_dept = [c for c in named if c.department in depts]
        if in_dept:
            return _out(in_dept, "birim_ad", len(in_dept) == 1)
        # söylenen birimde o ad yok ("satıştan Burak Bey"; Burak muhasebede): aktarılmaz, teyit sorulur
        return _out(named, "ad", False)
    if named and len(named) <= MAX_CANDIDATES:
        # hitapla ("Zeynep Hanım", "Can Bey", "Mehmet Albay") ya da soyadla ("Öztürk Bey") söylenen tek kişi: kesin
        # (kural modu ve LLM de doğrudan aktarıyordu); yalnız ad ("Mehmet var mı"): teyit
        sure = (len(named) == 1 and (_has_honorific(text) or _last_name_said(named[0], words))
                and not _other_given_name(index, named[0], words))       # "murat çelik" ≠ Burak Çelik: teyit
        return _out(named, "ad", sure)
    return _out([], "", False)


def department_said(index: DirectoryIndex, text: str) -> str | None:
    """Sözde tam olarak geçen tek birim ("muhasebe", "personel başkanlığından"); yoksa ya da birden çoksa None."""
    depts = _mentioned_departments(index, _without_self(text))
    return next(iter(depts)) if len(depts) == 1 else None


def has_honorific(text: str) -> bool:
    return _has_honorific(text)


def _has_honorific(text: str) -> bool:
    """Hitap ya da rütbe, ekli biçimleriyle ("beyi", "hanımla", "albayı")."""
    return any(w.startswith(h) and len(w) - len(h) <= 3 for w in fold_words(text) for h in _HONORIFIC_WORDS)


def _last_name_said(c: Contact, words: list[str]) -> bool:
    k = search_key(c.last_name)
    return bool(k) and any(similarity(v, k) >= NAME_SAME for w in words for v in query_variants(w))


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
    return any(any(similarity(v, k) >= NAME_SAME for v in query_variants(w)) for w in words for k in keys)


def _mentioned_departments(index: DirectoryIndex, words: list[str]) -> set[str]:
    """Sözde geçen birimler (adı ya da eş anlamlısı ardışık sözcüklerle; ekli biçimler: "bilgi işlemden")."""
    return {d for d, k in _dept_names(index)
            if any(all(_like(words[i + j], k[j]) for j in range(len(k))) for i in range(len(words) - len(k) + 1))}


def _normalize_unit_words(words: list[str]) -> list[str]:
    """STT'nin birim adlarında sık yaptığı bozmalar: 'masaj/taş bakanlığı' → 'asayiş başkanlığı',
    'alt/ama/nasıl tepe' → 'anıttepe', 'yandarma' → 'jandarma'."""
    out: list[str] = []
    i = 0
    while i < len(words):
        w = words[i]
        nxt = words[i + 1] if i + 1 < len(words) else ""
        if w in ("masaj", "tas") and nxt.startswith(("bakanl", "baskan")):
            out.append("asayis")
            i += 1
        elif w in ("alt", "ama", "nasil", "anit", "ana", "ali") and nxt.startswith(("tepe", "stepe")):
            out.append("anittepe")
            i += 2
        elif w.startswith("stepe"):
            out.append("anittepe")
            i += 1
        elif w.startswith("yandarma"):
            out.append("jandarma" + w[8:])
            i += 1
        else:
            out.append(w)
            i += 1
    return out


def _title_words(text: str) -> list[str]:
    """Unvan araması için sözcükler: katlanmış, unvan ve rütbe sözcükleri **atılmaz** ("avukat", "uzmanı",
    "astsubayı" makam hattında asıl sözcüktür; ad aramasında hitap gibi atılırlar); kendini tanıtma ve istek sözleri
    atılır."""
    raw = _normalize_unit_words(fold_words(text))
    drop = {raw[i + 1] for i, w in enumerate(raw[:-1]) if w in _SELF}
    return [w for w in raw if w not in drop and w not in _SELF and w not in _SOUND_STOP]


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


def _cached(index: DirectoryIndex, name: str, build: Any) -> Any:
    """Rehberden türetilen tablo, rehberin kendisinde (`index.derived`, her yeniden kurulmada boşalır): büyük rehberde
    (binlerce makam hattı) her sözde yeniden hesaplanmasın (ölçüm 05.11.2026: 3.500 kayıtla söz başına 0,5–1 sn →
    0,05–0,3 sn)."""
    if name not in index.derived:
        index.derived[name] = build()
    return index.derived[name]


def _dept_names(index: DirectoryIndex) -> list[tuple[str, list[str]]]:
    """(birim, ad / eş anlamlı sözcükleri) — tekil."""
    def build() -> list[tuple[str, list[str]]]:
        seen = {(c.department, tuple(search_key(name).split())) for c in index.contacts()
                for name in (c.department, *c.department_aliases)}
        return sorted((d, list(k)) for d, k in seen if k)
    out: list[tuple[str, list[str]]] = _cached(index, "dept_names", build)
    return out


def _person_keys(index: DirectoryIndex) -> list[tuple[Contact, str, str]]:
    """(kişi, ad anahtarı, soyad anahtarı) — kaynak rehberdeki tüm kişiler (aktarılamayanlar dahil)."""
    def build() -> list[tuple[Contact, str, str]]:
        return [(c, search_key(c.first_name), search_key(c.last_name))
                for c in index.source_contacts() if c.kind == "person"]
    out: list[tuple[Contact, str, str]] = _cached(index, "person_keys", build)
    return out


def _bucket(word: str) -> str:
    """`_like` ancak ilk 4 harfi aynı (ya da 4'ten kısa ve tıpatıp aynı) sözcükler arasında doğru olabilir."""
    if word.startswith("bakanl"):
        return "bask"
    return word[:4] if len(word) >= 4 else f"={word}"


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


def _words(text: str) -> set[str]:
    return set(search_key(text).split())


def _like(a: str, b: str) -> bool:
    """Aynı sözcüğün ekli biçimleri: biri ötekinin başı ("komutanıyla" ~ "komutanı", "kaymakamı" ~ "kaymakamlığı").
    Türkçe ek atmak yerine ortak baş: katmanlı eklerde ("yardımcısına") ek atıcı kökü tutarlı bulamıyor."""
    if a == b or {a[:6], b[:6]} == {"bakanl", "baskan"}:
        return True
    n = next((i for i, (x, y) in enumerate(zip(a, b, strict=False)) if x != y), min(len(a), len(b)))
    return n >= 4 and n >= min(len(a), len(b)) - 1


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
                            for w in words if any(_word_matches(w, x) for x in fold_words(f"{c.department} {c.title}")))]
        if dept_only:
            return dept_only
        rem_u = []
        for c in top:
            dept_w = set(fold_words(c.department)) | {"baskanligi", "bakanligi", "komutanligi", "mudurlugu", "sube", "subesi"}
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


# --- ses benzerliği (STT adı bozduğunda) ---------------------------------------------------------------------------
# Ölçüm 04.11.2026 (mihu, telefon): "Ayşe Yılmaz" → "ağlayamaz", "Burak Çelik" → "bırakçayı", "Mehmet Demir'e" →
# "mehmet de mi'ye". Yazım benzerliği ile ünsüz iskeleti benzerliğinin ortalaması; en iyi aday SOUND_MIN'i geçmeli ve
# ikinciden en az SOUND_MARGIN önde olmalı. Gerçek STT hatalarından 6/8'i doğru, ad içermeyen 29 sözün hiçbiri
# tetiklemedi. Eşikler: thresholds.SOUND_MIN / SOUND_MARGIN. Yalnız teyitle: yanlış eşleşmenin bedeli tek soru.
# eşleşmeye girmeyen sık sözcükler (katlanmış): istek fiilleri, soru ekleri
_SOUND_STOP = {"istiyorum", "istiyordum", "ariyorum", "aliyorum", "gorusmek", "gorusecektim", "musunuz", "misiniz",
               "misin", "musun", "ile", "bir", "lutfen", "baglar", "baglayin", "baglayabilir", "var", "mi", "mu",
               "icin", "beraber", "verir", "acaba", "rica", "ederim", "orada", "burada", "kim", "kimle", "kiminle", "istiyor", "istiyoruz", "isterim", "isteriz", "istemistim", "ariyor", "gorusebilir", "konusmak", "konusacaktim", "baglanmak", "baglanabilir", "ulasmak", "ulasabilir", "miyim", "muyum", "miyiz", "muyuz", "aktar", "aktarin", "aktarabilir", "merhaba", "iyi", "gunler", "selam", "alo"}


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



# --- birden çok hedef (arka planda ikinci konuşan) -----------------------------------------------------------------
# Ölçüm 05.11.2026 (sesli set, arkada ikinci konuşan 10 dB kısık): STT iki sözü karıştırıyor ("iki yüz on dört
# numaralı dahili Mehmet Demir'e bağlar mısınız"); hızlı yol ilk bulduğuna aktarıyordu: 80 kayıtta 18 yanlış eylem.


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


def _department_tails(index: DirectoryIndex, words: list[str]) -> set[str]:
    """Çok sözcüklü birimin bozuk ya da eksik söylenişi: yalnız ayırt edici son sözcük ("işlemden" → Bilgi İşlem,
    "kaynaklarıyla" → İnsan Kaynakları) ya da ilk sözcük + ikincinin başı ("teknik de" → Teknik Destek; "insanla
    görüşmek" değil). Karışık seste sözcüklerden biri düşüyor ya da bozuluyor. Yalnız birden çok hedef denetiminde:
    aktarma bununla yapılmaz."""
    def build() -> tuple[dict[str, set[str]], dict[tuple[str, str], set[str]]]:
        tails: dict[str, set[str]] = {}
        heads: dict[tuple[str, str], set[str]] = {}
        for d, k in _dept_names(index):
            if len(k) >= 2 and len(k[-1]) >= 5:
                tails.setdefault(k[-1], set()).add(d)
            if len(k) >= 2 and len(k[0]) >= 5:
                heads.setdefault((k[0], k[1][:2]), set()).add(d)
        return tails, heads
    tails, heads = _cached(index, "dept_parts", build)
    out = {d for w in words for tail, ds in tails.items() if len(ds) == 1 and _like(w, tail) for d in ds}
    for i, w in enumerate(words[:-1]):
        for (head, start), ds in heads.items():
            if len(ds) == 1 and _like(w, head) and words[i + 1].startswith(start):
                out |= ds
    return out


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
    words = _without_self(text)
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


# --- bozuk duyulan birim / konu (sesli set 05.11.2026: "masebeye", "çifremi", "teknikte") -------------------------
_PHON = str.maketrans("csz", "sss")      # katlanmış metinde ç→c, ş→s: mihu s / ş / ç / z'yi karıştırıyor


def _department_keys(index: DirectoryIndex) -> list[tuple[str, str]]:
    """(birim adının sözcüğü, birim) çiftleri; rehber sürümüne göre önbellekte."""
    def build() -> list[tuple[str, str]]:
        return sorted({(k, c.department) for c in index.contacts() for name in (c.department, *c.department_aliases)
                       for k in search_key(name).split()})
    out: list[tuple[str, str]] = _cached(index, "dept_keys", build)
    return out


def fuzzy_departments(index: DirectoryIndex, text: str, topics: TopicTable | None = None) -> set[str]:
    """Sözdeki uzun sözcüklerden birine benzeyen birimler: birim adının sözcüğü (yazım benzerliği ≥ FUZZY_TARGET_MIN)
    ya da konu sözcüğü (s / ş / ç / z denkleştirilip baş eşleşmesi: "çifremi" ~ "şifre" → Bilgi İşlem). Yalnız teyitle
    kullanılır."""
    keys = _department_keys(index)
    # yalnız gerçekten bozuk sözcükler: bir birimin sözcüğüne zaten uyan ("personel", "kaymakamı" ~ "kaymakamlığı")
    # ya da unvan sözcüğü olan ("valiyle") bulanık eşlemeye girmez
    words = [w for w in fold_words(text) if len(w) >= 5 and w not in _SOUND_STOP and not is_role_word(w)
             and not any(_like(w, k) for k, _ in keys)]
    if not words:
        return set()
    out = {d for k, d in keys if len(k) >= 5 and any(similarity(strip_suffix(w), k) >= FUZZY_TARGET_MIN
                                                     for w in words)}
    if topics is not None:
        phon = [w.translate(_PHON) for w in words]
        for phrases, topic in topics.topics:
            if any(len(p) == 1 and len(p[0]) >= 5 and any(w.startswith(p[0].translate(_PHON)) for w in phon)
                   for p in phrases) and (dept := topics.department_of(topic)):
                out.add(dept)
    return out
