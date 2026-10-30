"""Türkçe rehber araması (tasarim.md 5.5, 6.5, Ek C).

STT metni kusurludur: Türkçe karakterler farklı yazılabilir, ekler gelir ("muhasebeden", "Ayşe'yi"),
unvan ve hitaplar karışır ("Dr. Yılmaz", "Ayşe Hanım"), Whisper sayıları rakamla yazar.
Bu yüzden hem sorgu hem rehber aynı **arama anahtarına** indirgenir ve bulanık eşleştirilir.

Sınıflandırma (Match):
  - Eşik (`min_score`) üstündeki adaylar skora göre sıralanır.
  - En iyi adaya `margin` puandan yakın başka aday varsa → MULTIPLE (arayana sorulur).
  - Tek aday kaldıysa skor `high_score` ve üstü → SINGLE_HIGH, değilse SINGLE_LOW.
  - `transferable=false` ve silinmiş kayıtlar aramaya hiç girmez; LLM onları göremez (tasarim.md 10.1).
  - Yalnızca birim istenirse (ad yok) ve birimin genel hattı (`kind="department"`) varsa o önerilir.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

import numpy as np
from rapidfuzz import fuzz, process

from sesli_asistan.libs.text.normalizer import strip_punct, turkish_lower

from .models import Candidate, Contact, Match, SearchResult

_FOLD = str.maketrans("çğıöşüâîû", "cgiosuaiu")

# Unvanlar ve hitaplar: eşleşmede ad değildir, atılır (Ek C madde 3–4)
_TITLES = {"dr", "doktor", "doc", "docent", "prof", "profesor", "av", "avukat", "uzm", "uzman",
           "op", "operator", "muh", "muhendis", "ogr", "gor", "sayin"}
_HONORIFICS = {"hanim", "hanimefendi", "bey", "beyefendi", "hoca", "hocam", "abla", "abi"}
# Genel birim sözcükleri: "satış departmanı", "finans birimi" → "satış", "finans"
_GENERIC = {"departman", "birim", "bolum", "servis", "ofis", "sube"}
_STOP = _TITLES | _HONORIFICS | _GENERIC

# Görev/unvan sözcükleri: birim alanında geçerse sorgu bir kişiyi (unvanıyla) arıyordur: "satış müdürü"
ROLE_WORDS = {"mudur", "muduru", "mudurunu", "uzman", "uzmani", "sef", "sefi", "sorumlu", "sorumlusu",
              "yonetici", "yoneticisi", "direktor", "direktoru", "asistan", "asistani", "sekreter", "sekreteri"}


def has_role_word(text: str) -> bool:
    return any(w in ROLE_WORDS or strip_suffix(w) in ROLE_WORDS for w in search_key(text).split())

_ONES = ["", "bir", "iki", "üç", "dört", "beş", "altı", "yedi", "sekiz", "dokuz"]
_TENS = ["", "on", "yirmi", "otuz", "kırk", "elli", "altmış", "yetmiş", "seksen", "doksan"]


def number_to_words(n: int) -> str:
    """0–999999 arası tamsayının Türkçe okunuşu (Ek C madde 6). 100 → "yüz", 1000 → "bin"."""
    if n == 0:
        return "sıfır"
    if not 0 < n < 1_000_000:
        return str(n)
    parts = []
    thousands, rest = divmod(n, 1000)
    if thousands:
        if thousands > 1:
            parts.append(number_to_words(thousands))
        parts.append("bin")
    hundreds, rest = divmod(rest, 100)
    if hundreds:
        if hundreds > 1:
            parts.append(_ONES[hundreds])
        parts.append("yüz")
    tens, ones = divmod(rest, 10)
    parts += [w for w in (_TENS[tens], _ONES[ones]) if w]
    return " ".join(parts)


_DIGITS = re.compile(r"\d+")

# Hâl ekleri (katlanmış biçimde, uzundan kısaya). STT kesme işaretini çoğu zaman yazmaz:
# "muhasebeden", "zeynep hanımı". Ek atılmış biçim yalnızca **ek aday** olarak denenir, asıl biçim
# korunur; kök en az 4 harf kalmalı ("ayşe" → "ays" denenmez).
_SUFFIXES = ("ndan", "nden", "sine", "sina", "sini", "sinda", "sinde", "dan", "den", "tan", "ten",
             "nin", "nun", "yla", "yle", "ine", "ina", "ini", "una", "une", "unu", "la", "le", "ya", "ye",
             "yi", "yu", "da", "de", "ta", "te", "i", "u", "a", "e")


def strip_suffix(word: str) -> str:
    for suf in _SUFFIXES:
        if word.endswith(suf) and len(word) - len(suf) >= 4:
            return word[: -len(suf)]
    return word


def search_key(text: str) -> str:
    """Sorguyu ve rehber alanlarını karşılaştırılabilir biçime indirger."""
    t = unicodedata.normalize("NFC", text or "")
    t = re.sub(r"['’]\w*", "", t)                       # kesmeden sonraki ek: "Ayşe'yi" → "Ayşe"
    t = _DIGITS.sub(lambda m: f" {number_to_words(int(m.group()))} ", t)
    t = strip_punct(turkish_lower(t)).translate(_FOLD)
    return " ".join(w for w in t.split() if w not in _STOP and strip_suffix(w) not in _STOP)


def alias_key(text: str) -> str:
    """Takma ad için anahtar: unvan / hitap **atılmaz** ("Ayşe Abla" ↔ "Ayşe Hanım" ayrı), ekler atılır."""
    t = unicodedata.normalize("NFC", text or "")
    t = re.sub(r"['’]\w*", "", t)
    t = strip_punct(turkish_lower(t)).translate(_FOLD)
    return " ".join(strip_suffix(w) for w in t.split())


def query_variants(key: str) -> list[str]:
    """Arama anahtarı + ekleri atılmış biçimi (farklıysa)."""
    stripped = " ".join(strip_suffix(w) for w in key.split())
    return [key] if stripped == key else [key, stripped]


HOTWORD_CHARS = 400


class DirectoryIndex:
    """Bellek içi arama dizini. Rehber değişince `rebuild()` ile yeniden kurulur."""

    def __init__(self, contacts: Iterable[Contact] = (), min_score: int = 70, high_score: int = 90,
                 margin: int = 5, limit: int = 3):
        self.min_score, self.high_score, self.margin, self.limit = min_score, high_score, margin, limit
        self._entries: list[tuple[Contact, list[str], list[str]]] = []
        self._contacts: list[Contact] = []
        self.extra_aliases: dict[str, tuple[str, ...]] = {}   # ayar ekranından: kayıt kimliği → ek takma adlar
        self.local_contacts: list[Contact] = []               # ayar ekranından eklenen kayıtlar (kaynakta yok)
        self.rebuild(contacts)

    def set_extra_aliases(self, aliases: dict[str, Iterable[str]]) -> None:
        """Rehber kaynağında olmayan, yerelde eklenen takma adlar ("Ferhat Bey" → Ferhat Yıldırım). Rehber her
        güncellendiğinde yeniden uygulanır."""
        self.set_overlay(aliases, self.local_contacts)

    def set_overlay(self, aliases: dict[str, Iterable[str]], local_contacts: Iterable[Contact]) -> None:
        """Yerel katman (ayar ekranı): takma adlar ve yerel kayıtlar. Kaynak rehber her güncellendiğinde yeniden
        uygulanır; kaynağın kendisi değişmez."""
        self.extra_aliases = {k: tuple(v) for k, v in aliases.items() if v}
        self.local_contacts = list(local_contacts)
        self.rebuild(self._contacts)

    def source_contacts(self) -> list[Contact]:
        """Kaynak rehberdeki kayıtlar (yerel katman hariç, silinmiş hariç)."""
        return [c for c in self._contacts if not c.deleted]

    def contacts(self) -> list[Contact]:
        """Aranabilir kayıtlar (ayar ekranında takma ad eklemek için)."""
        return [c for c, _, _ in self._entries]

    def rebuild(self, contacts: Iterable[Contact]) -> None:
        self._contacts = list(contacts)
        entries = []
        for c in (*self._contacts, *self.local_contacts):
            if c.deleted or not c.transferable:
                continue
            extra = self.extra_aliases.get(c.id, ())
            names = [c.full_name, c.first_name, c.last_name, c.title, *c.aliases, *extra]
            depts = [c.department, *c.department_aliases, *(extra if c.kind == "department" else ())]
            entries.append((c, _keys(names), _keys(depts)))
        self._entries = entries
        self._name_ix = _KeyTable([n for _, n, _ in entries])
        self._dept_ix = _KeyTable([d for _, _, d in entries])
        self._cache: dict[tuple[str, str], SearchResult] = {}
        # Ayar ekranında eklenen takma adların tam biçimi: "Ayşe Abla" arama anahtarında "ayse"ye iner ve iki Ayşe'yle
        # çakışır; tam söylenirse (hitabıyla) yalnızca sahibi bulunur (27.10.2026 deneme). Yalnız yerel takma adlar:
        # yönetici bunu bilerek, bulunamayan bir aramadan ekler. Rehber kaynağındaki "Ayşe Hanım" gibi genel hitaplı
        # takma adlar bulanık aramada kalır (aynı adlı başka biri de "Ayşe Hanım" diye aranabilir).
        self._aliases: dict[str, set[str]] = {}
        for c, _, _ in entries:
            for a in self.extra_aliases.get(c.id, ()):
                if k := alias_key(a):
                    self._aliases.setdefault(k, set()).add(c.id)
        # kişilerin ad ve soyad sözcükleri (unvan hariç): "sözde bir kişi geçiyor mu" sorusu için
        self.person_words = {w for c, _, _ in entries if c.kind == "person"
                             for w in search_key(f"{c.first_name} {c.last_name}").split() if len(w) >= 3}

    def __len__(self) -> int:
        return len(self._entries)

    def alias_match(self, text: str) -> Contact | None:
        """Sözde bir takma ad **tam** (hitabıyla) geçiyorsa ve tek kişiye aitse o kişi. Tek kelimelik takma ad yalnız
        sözün tamamıysa sayılır (ör. "IT")."""
        q = alias_key(text)
        if not q:
            return None
        ids: set[str] = set()
        for k, owners in self._aliases.items():
            if k == q or (" " in k and f" {k} " in f" {q} "):
                ids |= owners
        return self.get(next(iter(ids))) if len(ids) == 1 else None

    def get(self, contact_id: str) -> Contact | None:
        return next((c for c, _, _ in self._entries if c.id == contact_id), None)

    def search(self, name: str = "", department: str = "", extension: str = "") -> SearchResult:
        ext = "".join(ch for ch in str(extension or "") if ch.isdigit())
        if ext:  # arayan dahili numarayı söyledi: yalnızca tam eşleşme
            hits = [Candidate(c, 100) for c, _, _ in self._entries if c.extension == ext]
            return SearchResult(tuple(hits[:1]), Match.SINGLE_HIGH if hits else Match.NONE)
        if name and not department and (c := self.alias_match(name)) is not None:
            return SearchResult((Candidate(c, 100),), Match.SINGLE_HIGH)
        qn, qd = search_key(name), search_key(department)
        if not qn and not qd:
            return SearchResult((), Match.NONE)
        if (hit := self._cache.get((qn, qd))) is not None:
            return hit
        res = self._search(qn, qd)
        if len(self._cache) >= 4096:
            self._cache.clear()
        self._cache[(qn, qd)] = res
        return res

    def _search(self, qn: str, qd: str) -> SearchResult:
        """Puanlar kayıt başına değil, **benzersiz anahtar** başına bir kez hesaplanır; önce rapidfuzz'ın C ile
        yazılmış toplu karşılaştırmasıyla (cdist) her anahtar için üst sınır bulunur, yalnız eşiği geçebilecek
        anahtarlarda tam `similarity` hesaplanır. Sonuç eski kayıt-kayıt döngüsüyle birebir aynıdır (test:
        test_search.py), 3000 kayıtlı rehberde ~30 kat hızlı: arama olay döngüsünü bloklamaz."""
        if not qn and not qd:
            return SearchResult((), Match.NONE)
        vn, vd = query_variants(qn), query_variants(qd)
        total = np.full(len(self._entries), np.inf)
        if qn:
            total = np.minimum(total, self._name_ix.entry_scores(vn, self.min_score))
        if qd:
            total = np.minimum(total, self._dept_ix.entry_scores(vd, self.min_score, subset=True))
        scored = [Candidate(self._entries[i][0], int(total[i])) for i in np.flatnonzero(total >= self.min_score)]
        scored.sort(key=lambda x: (-x.score, x.contact.full_name))
        if qd and not qn:
            # yalnızca birim istendi: birimin genel hattı varsa kişiler yerine o önerilir
            lines = [x for x in scored if x.contact.kind == "department"]
            scored = lines or scored
        if not scored:
            return SearchResult((), Match.NONE)
        top = scored[0].score
        close = [x for x in scored if x.score >= top - self.margin]
        if len(close) > 1:
            return SearchResult(tuple(close[: self.limit]), Match.MULTIPLE, total=len(close))
        match = Match.SINGLE_HIGH if top >= self.high_score else Match.SINGLE_LOW
        return SearchResult((scored[0],), match)

    def hotwords(self, max_chars: int = HOTWORD_CHARS, priority: Iterable[str] = (),
                 focus: Iterable[Candidate] = ()) -> list[str]:
        """STT'ye ipucu: birimler önce, sonra ad soyadlar; toplam uzunluk `max_chars` ile sınırlı.

        Whisper'da ipucu, önceki metin istemine yazılır ve en çok 223 belirteç (token) alır; fazlası
        faster-whisper tarafından kesilir. Türkçe adlar belirteç başına ~2,5–3 harf olduğundan 400 harf ≈
        130–160 belirteç. Sayıyla sınırlamak (eski: 60 öğe) uzun adlarda sınırı aşıp sonu kesiyordu.
        Büyük rehberde tüm adlar sığmaz; sıra (tasarim.md 5.2.1):
          1. `focus`: son aramanın adayları ve birimleri (arayan "hangi Ayşe?" sorusuna bunlardan birini söyler),
          2. `priority`: en çok aranan dahililerin kişileri (çağrı geçmişinden),
          3. birimler, 4. kalan adlar (alfabetik)."""
        persons = [c for c, _, _ in self._entries if c.full_name and c.kind != "department"]
        by_ext: dict[str, list[str]] = {}
        for c in persons:
            by_ext.setdefault(c.extension, []).append(c.full_name)
        focused: list[str] = []
        for cand in focus:
            c = cand.contact
            focused += [c.full_name] if c.kind != "department" and c.full_name else []
            focused += [c.department] if c.department else []
        ranked = [n for ext in priority for n in by_ext.get(ext, [])]
        depts = sorted({c.department for c, _, _ in self._entries if c.department})
        names = sorted({c.full_name for c in persons})
        out, used, seen = [], 0, set()
        for w in focused + ranked + depts + names:
            if w in seen:
                continue
            if used + len(w) + 1 > max_chars:
                continue    # daha kısa olan sonraki ad sığabilir
            seen.add(w)
            out.append(w)
            used += len(w) + 1
        return out


class _KeyTable:
    """Kayıtların arama anahtarları, benzersiz anahtar listesi üzerinden (toplu puanlama için)."""

    def __init__(self, per_entry: list[list[str]]):
        self.keys = sorted({k for ks in per_entry for k in ks})
        pos = {k: i for i, k in enumerate(self.keys)}
        self.words = [set(k.split()) for k in self.keys]
        self.nwords = np.array([len(w) for w in self.words])
        flat: list[int] = []
        starts: list[int] = []
        for ks in per_entry:
            starts.append(len(flat))
            flat.extend(pos[k] for k in ks)
            if not ks:
                flat.append(len(self.keys))           # boş: puanı 0 olan yalancı anahtar
        self.flat = np.array(flat, dtype=np.int64)
        self.starts = np.array(starts, dtype=np.int64)

    def entry_scores(self, queries: list[str], min_score: int, subset: bool = False) -> np.ndarray:
        if not len(self.starts):
            return np.zeros(0)
        per_key = np.zeros(len(self.keys) + 1)
        if self.keys:
            # üst sınır: similarity ≤ max(ratio, token_set, 0.9·partial) — eşiği geçemeyecek anahtar elenir
            bound = np.zeros(len(self.keys))
            for q in queries:
                bound = np.maximum(bound, process.cdist([q], self.keys, scorer=fuzz.ratio)[0])
                bound = np.maximum(bound, process.cdist([q], self.keys, scorer=fuzz.token_set_ratio)[0])
                if len(q.split()) == 1 and len(q) >= 4:
                    bound = np.maximum(bound, 0.9 * process.cdist([q], self.keys, scorer=fuzz.partial_ratio)[0])
            for ix in np.flatnonzero(bound >= min_score - 1):
                per_key[ix] = max(similarity(q, self.keys[ix]) for q in queries)
            if subset:
                # birim adının bütün kelimeleri sorguda geçiyorsa SUBSET_SCORE (_best ile aynı kural)
                qsets = [set(q.split()) for q in queries]
                for i, w in enumerate(self.words):
                    if per_key[i] < SUBSET_SCORE and any(w <= qt for qt in qsets):
                        per_key[i] = SUBSET_SCORE
        return np.round(np.maximum.reduceat(per_key[self.flat], self.starts))


def _keys(values: Iterable[str]) -> list[str]:
    return [k for k in (search_key(v) for v in values if v) if k]


def similarity(q: str, k: str) -> float:
    """Sorgu ↔ rehber alanı benzerliği (0–100).

    - Tam benzerlik (`ratio`) her zaman.
    - Kelime kümesi benzerliği yalnızca sorgu alandan **kısa ya da eşit** olduğunda: "ayse" ↔ "ayse yilmaz"
      tam puan alır, ama "aysel yilmaz" ↔ "yilmaz" (sorgu daha uzun) tam puan almaz; aksi hâlde yalnızca
      soyadı tutan herkes en yüksek skoru alırdı.
    - Kısmi benzerlik (%10 indirimli) yalnızca tek kelimelik ve en az 4 harfli sorguda: "ik" ↔ "teknik"
      eşleşmesin.
    """
    s = fuzz.ratio(q, k)
    nq, nk = len(q.split()), len(k.split())
    if nq <= nk:
        s = max(s, fuzz.token_set_ratio(q, k))
    if nq == 1 and len(q) >= 4 and len(k) >= 4:
        s = max(s, 0.9 * fuzz.partial_ratio(q, k))
    if s > EXTENDED_CAP and _extends_word(q, k):
        s = EXTENDED_CAP
    return s


# Sorgudaki kelime rehberdeki kelimeyi içerip ek olmayan harflerle uzuyorsa ("demirci" ↔ "demir", "ayşegül" ↔
# "ayşe", "canan" ↔ "can") büyük olasılıkla başka bir addır: skor net eşleşme eşiğinin altına çekilir, aktarmadan
# önce arayana sorulur (single_low). Hâl ekleri ("demire", "kaynaklarına") etkilenmez.
EXTENDED_CAP = 85
_ENDINGS = frozenset(_SUFFIXES) | {"na", "ne", "ni", "nu", "nda", "nde"}


def _extends_word(q: str, k: str) -> bool:
    kws = k.split()
    for w in q.split():
        for kw in kws:
            if len(kw) >= 3 and len(w) > len(kw) and w.startswith(kw) and w[len(kw):] not in _ENDINGS:
                if w not in kws:
                    return True
    return False


SUBSET_SCORE = 95


def _best(queries: list[str], keys: list[str], subset: bool = False) -> int:
    """subset=True (yalnızca birim alanı): birim adının bütün kelimeleri sorguda geçiyorsa SUBSET_SCORE.
    "arıza kaydı" → "arıza" birimi; "muhasebe işleri" → "muhasebe". Ad alanında kapalıdır: "aysel yılmaz"
    sorgusu yalnızca soyadı tutan herkesle eşleşmesin."""
    if not keys:
        return 0
    best = max(similarity(q, k) for q in queries for k in keys)
    if subset and best < SUBSET_SCORE:
        for q in queries:
            qt = set(q.split())
            if any(set(k.split()) <= qt for k in keys):
                best = SUBSET_SCORE
                break
    return round(best)
