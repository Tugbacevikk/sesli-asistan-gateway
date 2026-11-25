"""Birim adı: sözde geçen birimler, birim adının sonu, bozuk duyulan birim (ses benzerliği)."""
from __future__ import annotations

from ..search import (
    UNIT_NOUNS,
    DirectoryIndex,
    fold_words,
    is_role_word,
    search_key,
    similarity,
    strip_suffix,
)
from ..thresholds import FUZZY_TARGET_MIN
from ..topics import TopicTable
from ..words import same_word
from .common import (
    _SOUND_STOP,
    _cached,
    without_self,
)


def department_said(index: DirectoryIndex, text: str) -> str | None:
    """Sözde tam olarak geçen tek birim ("muhasebe", "personel başkanlığından"); yoksa ya da birden çoksa None."""
    depts = _mentioned_departments(index, without_self(text))
    return next(iter(depts)) if len(depts) == 1 else None


def _mentioned_departments(index: DirectoryIndex, words: list[str]) -> set[str]:
    """Sözde geçen birimler (adı ya da eş anlamlısı ardışık sözcüklerle; ekli biçimler: "bilgi işlemden")."""
    return set(department_spans(index, words))


def department_spans(index: DirectoryIndex, words: list[str]) -> dict[str, set[int]]:
    """Sözde geçen birim → adının (ya da eş anlamlısının) kapladığı sözcük sıraları (bütün geçişler birlikte)."""
    out: dict[str, set[int]] = {}
    for d, k in _dept_names(index):
        for i in range(len(words) - len(k) + 1):
            if all(same_word(words[i + j], k[j]) for j in range(len(k))):
                out.setdefault(d, set()).update(range(i, i + len(k)))
    return out


def drop_dominated(spans: dict[str, set[int]]) -> set[str]:
    """Sözcükleri başka bir birimin eşleşmesinin içinde kalan birimler atılır: "dereköy nüfus müdürlüğü" →
    Dereköy İlçe Nüfus (Akçakaya'nın "nüfus"u ve Dereköy Kaymakamlığı'nın "dereköy"ü onun parçası); "valilik hukuk
    işleri" → Hukuk İşleri (Valilik onun parçası). Ayrı sözcüklerle söylenen iki birim ("muhasebe ve satış") kalır.
    Sesli deneme listesi 25.11.2026: parça eşleşmeler "Akçakaya mı, Dereköy mü?" diye soruluyordu."""
    return {d for d, s in spans.items() if not any(e != d and s < t for e, t in spans.items())}


def _dept_names(index: DirectoryIndex) -> list[tuple[str, list[str]]]:
    """(birim, ad / eş anlamlı sözcükleri) — tekil."""
    def build() -> list[tuple[str, list[str]]]:
        seen = {(c.department, tuple(search_key(name).split())) for c in index.contacts()
                for name in (c.department, *c.department_aliases)}
        return sorted((d, list(k)) for d, k in seen if k)
    out: list[tuple[str, list[str]]] = _cached(index, "dept_names", build)
    return out


def _department_tails(index: DirectoryIndex, words: list[str]) -> set[str]:
    return set(department_tail_spans(index, words))


def department_tail_spans(index: DirectoryIndex, words: list[str]) -> dict[str, set[int]]:
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
    out: dict[str, set[int]] = {}
    for i, w in enumerate(words):
        for tail, ds in tails.items():
            if len(ds) == 1 and same_word(w, tail):
                for d in ds:
                    out.setdefault(d, set()).add(i)
    for i, w in enumerate(words[:-1]):
        for (head, start), ds in heads.items():
            if len(ds) == 1 and same_word(w, head) and words[i + 1].startswith(start):
                for d in ds:
                    out.setdefault(d, set()).update((i, i + 1))
    return out


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
    # birim türü sözcüğü ("şubeye", "müdürlüğüne") bozuk birim adı değildir: "narkotik şubeye" Milli Eğitim'in
    # şubelerine benzetilip "Emniyet mi, Milli Eğitim mi?" soruluyordu (sesli deneme listesi S05, 25.11.2026)
    words = [w for w in fold_words(text) if len(w) >= 5 and w not in _SOUND_STOP and not is_role_word(w)
             and not any(same_word(w, k) for k, _ in keys)
             and not any(same_word(w, u) or strip_suffix(w) == u for u in UNIT_NOUNS)]
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
