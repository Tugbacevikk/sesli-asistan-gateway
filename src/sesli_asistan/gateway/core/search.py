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

from rapidfuzz import fuzz

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
        self.rebuild(contacts)

    def rebuild(self, contacts: Iterable[Contact]) -> None:
        entries = []
        for c in contacts:
            if c.deleted or not c.transferable:
                continue
            names = [c.full_name, c.first_name, c.last_name, c.title, *c.aliases]
            depts = [c.department, *c.department_aliases]
            entries.append((c, _keys(names), _keys(depts)))
        self._entries = entries
        # kişilerin ad ve soyad sözcükleri (unvan hariç): "sözde bir kişi geçiyor mu" sorusu için
        self.person_words = {w for c, _, _ in entries if c.kind == "person"
                             for w in search_key(f"{c.first_name} {c.last_name}").split() if len(w) >= 3}

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, contact_id: str) -> Contact | None:
        return next((c for c, _, _ in self._entries if c.id == contact_id), None)

    def search(self, name: str = "", department: str = "", extension: str = "") -> SearchResult:
        ext = "".join(ch for ch in str(extension or "") if ch.isdigit())
        if ext:  # arayan dahili numarayı söyledi: yalnızca tam eşleşme
            hits = [Candidate(c, 100) for c, _, _ in self._entries if c.extension == ext]
            return SearchResult(tuple(hits[:1]), Match.SINGLE_HIGH if hits else Match.NONE)
        qn, qd = search_key(name), search_key(department)
        vn, vd = query_variants(qn), query_variants(qd)
        if not qn and not qd:
            return SearchResult((), Match.NONE)
        scored: list[Candidate] = []
        for c, name_keys, dept_keys in self._entries:
            parts = []
            if qn:
                parts.append(_best(vn, name_keys))
            if qd:
                parts.append(_best(vd, dept_keys, subset=True))
            score = min(parts)  # verilen her alan eşleşmeli
            if score >= self.min_score:
                scored.append(Candidate(c, score))
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
            return SearchResult(tuple(close[: self.limit]), Match.MULTIPLE)
        match = Match.SINGLE_HIGH if top >= self.high_score else Match.SINGLE_LOW
        return SearchResult((scored[0],), match)

    def hotwords(self, max_chars: int = HOTWORD_CHARS) -> list[str]:
        """STT'ye ipucu: birimler önce, sonra ad soyadlar; toplam uzunluk `max_chars` ile sınırlı.

        Whisper'da ipucu, önceki metin istemine yazılır ve en çok 223 belirteç (token) alır; fazlası
        faster-whisper tarafından kesilir. Türkçe adlar belirteç başına ~2,5–3 harf olduğundan 400 harf ≈
        130–160 belirteç. Sayıyla sınırlamak (eski: 60 öğe) uzun adlarda sınırı aşıp sonu kesiyordu.
        Büyük rehberde tüm adlar sığmaz: birimler ve sığan adlar gider (tasarim.md 5.2, açık konu)."""
        depts = sorted({c.department for c, _, _ in self._entries if c.department})
        names = sorted({c.full_name for c, _, _ in self._entries if c.full_name and c.kind != "department"})
        out, used = [], 0
        for w in depts + [n for n in names if n not in depts]:
            if used + len(w) + 1 > max_chars:
                continue    # daha kısa olan sonraki ad sığabilir
            out.append(w)
            used += len(w) + 1
        return out


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
    if nq == 1 and len(q) >= 4:
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
