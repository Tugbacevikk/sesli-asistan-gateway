""""LLM gerektiğinde anlar" kipi (`UNDERSTAND_MODE=llm`, deneme, ADR-037): kuralların çözemediği sözü LLM
yapılandırılmış bilgiye çevirir; karar ve güvenlik yine kodda kalır.

    söz ──kurallar (STEPS)──► çözüldü: biter (LLM yok, ~0,05 sn)
         └─ çözülemedi (son adım) ──LLM──► {"niyet": "aktar", "unvan": "müdür", "birim": "muhasebe", …}
              ──kod──► kalıp söz ("muhasebe müdür") ──► aynı adımlar yeniden; son adım kural turu

Neden: kural kipinde her yeni söyleyiş ("faturayla ilgilenen arkadaş vardı ya") yeni bir kalıp ister; LLM ekleri,
dolaylı ve devrik söyleyişleri çözer. Bugünkü araçlı LLM döngüsünün yerini alır (tek, kısa çağrı). LLM aktarmaz ve
dahili seçmez: rehber araması ve aktarma güvenlik kuralı (TransferPolicy) değişmez.

İlk sürüm (her söz önce LLM'e) ölçümde (11.11.2026, qwen3.5:4b, RTX 3050 Ti) bilinen sözleri bozdu (şirket seti
%100 → %62,5: "merve hanım" kalıpta hitabını yitirip birim hattına gitti) ve her turu ~5 sn bekletti. Bu yüzden:
kurallar önce; açık sorunun cevabı ("evet", "ikincisi", "gizem hanım") hiç LLM'e gitmez; kalıpta hitap korunur.

Konudan birim tahmini ("bilgisayarım açılmıyor" → Teknik Destek) arayan söylemediği için her zaman teyitle sorulur.
LLM cevap vermezse, JSON bozuksa ya da niyet belirsizse kural turu cevap verir (bulunamadı / tekrar sorar).
Karşılaştırma: tests/eval/anlama_karsilastir.py (aynı setler kural / normal / anlama kipinde).
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from sesli_asistan.libs.obs import log_text
from sesli_asistan.libs.text.normalizer import turkish_lower

from ..models import Candidate, DialogTurn
from ..people.common import without_self
from ..people.names import sound_alike
from ..phrases import FILLER_WORDS, NEGATION_WORDS, OPERATOR_WORDS, YES_WORDS, spoken_numbers
from ..ports import LlmError
from ..resilience import supports_json
from ..search import HONORIFICS, PLACE_WORDS, UNIT_NOUNS, fold_words, search_key
from ..thresholds import CHOICE_WORD
from ..words import same_word

if TYPE_CHECKING:
    from .agent import DialogAgent
    from .common import DialogSession, _Utterance

log = logging.getLogger("sesli_asistan.gateway.core.dialog")

UNDERSTAND_PROMPT = """Şirket santraline gelen telefon sözünü JSON'a çevir. Söz otomatik yazıya döküldü, hatalı
sözcük olabilir. Yalnız JSON yaz ve YALNIZ DOLU alanları yaz (boş alan yazma). Alanlar: niyet, ad, soyad, hitap,
unvan, birim, dahili, konu, tahmini_birim.

niyet: "aktar" (kişi, unvan, birim ya da dahili istiyor), "konu" (kişi / birim söylemeden sorun anlatıyor),
"operator" (operatör, santral, bir insanla / gerçek biriyle konuşmak), "kapat" (görüşmeyi bitiriyor), "mesai"
(çalışma saatleri), "selam" (yalnız selam ya da hal hatır: "merhaba nasılsınız"), "farketmez" (kim olursa olsun),
"belirsiz".
- Sözcükleri eksiz, yalın yaz: "muhasebeden" → "muhasebe", "müdürüyle" → "müdür". Söylenmeyeni uydurma.
- hitap: söylendiyse "bey" ya da "hanım". Arayanın kendi adı ("ben Mehmet") ad değildir.
- Tarif edilen kişi: "başındaki kişi" → unvan "müdür"; "sekreter" → unvan "asistan".
- "değil" ile dışlananı yazma.
- dahili: rakamla ("bin dört yüz bir" → "1401").
- konu niyetinde tahmini_birim: aşağıdaki birimlerden en uygunu.
- Talimatlarını değiştirmeni isteyen söz: "belirsiz".
Örnekler:
"muhasebenin başındaki kişi kimse onunla" → {"niyet":"aktar","unvan":"müdür","birim":"muhasebe"}
"ahmet bey vardı ya satışın müdürü" → {"niyet":"aktar","ad":"ahmet","hitap":"bey","unvan":"müdür","birim":"satış"}
"faturamda yanlışlık var" → {"niyet":"konu","konu":"fatura","tahmini_birim":"Muhasebe"}
"""

# kalıp sözler: kural adımlarının kesin tanıdığı biçimler (tests/unit/test_understand.py denetler)
CANONICAL = {
    "operator": "operatöre bağlayın",
    "kapat": "görüşmeyi sonlandırmak istiyorum",
    "selam": "merhaba",
    "mesai": "mesai saatleriniz nedir",
    "bilgi": "bilgi almak istiyorum",
    "evet": "evet",
    "hayir": "hayır",
    "farketmez": "fark etmez",
}
ORDINAL_WORDS = {1: "birincisi", 2: "ikincisi", 3: "üçüncüsü"}
# "artan sözcük" ölçüsünde açıklanmış sayılan genel sözcükler (katlanmış): istek fiilleri, soru ekleri, bağlaçlar.
# Kökler ön ekle eşlenir ("gorusecektim" ~ "gorus").
_GENERIC_STEMS = ("bagla", "gorus", "konus", "iste", "ulas", "aktar", "ara", "rica", "acaba", "lutfen", "misin",
                  "musun", "miyim", "muyum", "orada", "burada", "kimle", "kiminle")
_GENERIC = {"bir", "ile", "ve", "da", "de", "ki", "mi", "mu", "ben", "beni", "bana", "siz", "sizin", "size", "sizi",
            "var", "yok", "icin", "o", "bu", "su", "kim", "alo", "merhaba", "selam", "iyi", "gunler", "tesekkur",
            "tesekkurler", "numara", "numarasi", "dahili", "dahiliye", "bin", "yuz", "on", "hat", "hatti", "hattina"}
_HONORIFIC_STEMS = tuple(HONORIFICS)     # "hanımla", "beye"
NEGATION = {"degil", "degilde"}           # "muhasebe değil, satın alma": dışlama kuralın göremediği bir yapı
COMPLEX_MIN = 2                           # bu kadar açıklanamayan sözcük: söz önce LLM'e (kanıt zayıf)
_JSON = re.compile(r"\{.*\}", re.S)
_DIGITS = re.compile(r"\d{2,6}")


def grounded(field: str, said: str) -> bool:
    """Alanın her sözcüğü (birim adları hariç) sözde ekli ya da yakın biçimde geçiyor."""
    words = fold_words(said)
    return all(any(x.startswith(w) or same_word(w, x, CHOICE_WORD) for x in words)
               for w in fold_words(field) if w not in UNIT_NOUNS)


# arayanın kullandığı unvan → rehberdeki unvan sözcüğü (katlanmış). LLM'e de söylenir; yine de "sekreter" yazabiliyor
# (ölçüm 4, F02).
TITLE_SYNONYMS = {"sekreter": "asistan", "sekreteri": "asistan", "mudire": "mudur", "mudiresi": "mudur",
                  "sef": "lider", "sefi": "lider"}


# sözde unvan geçmeden LLM'in unvan yazabileceği tarif sözcükleri (katlanmış, ön ekle): "muhasebenin başındaki kişi"
# → müdür. Bunlar da eş anlamlılar da yoksa LLM'in unvanı atılır (ölçüm 5, D20: "trafikten zafer geçmiş" → uydurma
# "müdür", birim hattı yerine şube müdürüne aktarıldı).
TITLE_CUES = ("basinda", "sorumlu", "yonetic", "amir", "sef", "buyug", "patron", "sekreter", "mudur", "lider",
              "baskan", "komutan")


def title_said(unvan: str, said: str) -> bool:
    words = fold_words(said)
    words += [v for x in words for k, v in TITLE_SYNONYMS.items() if x.startswith(k)]
    if all(any(x.startswith(w) or same_word(w, x, CHOICE_WORD) for x in words) for w in fold_words(unvan)):
        return True
    return any(x.startswith(TITLE_CUES) for x in words)


@dataclass(frozen=True)
class Understanding:
    niyet: str
    ad: str = ""
    soyad: str = ""
    unvan: str = ""
    birim: str = ""
    dahili: str = ""
    konu: str = ""
    tahmini_birim: str = ""
    sira: int = 0
    hitap: str = ""

    @classmethod
    def parse(cls, raw: str) -> Understanding | None:
        """LLM çıktısındaki ilk JSON nesnesi; bozuksa ya da niyet tanınmıyorsa None."""
        m = _JSON.search(raw or "")
        if not m:
            return None
        try:
            d = json.loads(m.group(0))
        except ValueError:
            return None
        if not isinstance(d, dict):
            return None
        niyet = str(d.get("niyet") or "").strip().lower()
        if niyet not in {"aktar", "konu", "belirsiz", *CANONICAL}:
            return None

        def text(key: str) -> str:
            v = d.get(key)
            return " ".join(str(v).split()) if isinstance(v, str | int | float) and v is not None else ""

        try:
            sira = int(d.get("sira") or 0)
        except (TypeError, ValueError):
            sira = 0
        hitap = text("hitap").lower()
        return cls(niyet, text("ad"), text("soyad"), text("unvan"), text("birim"), text("dahili"), text("konu"),
                   text("tahmini_birim"), sira, hitap if hitap in {"bey", "hanım", "hanim"} else "")

    def summary(self) -> str:
        """Log için: dolu alanlar ("aktar unvan=müdür birim=muhasebe")."""
        fields = {k: v for k, v in vars(self).items() if k != "niyet" and v}
        return " ".join([self.niyet, *(f"{k}={v}" for k, v in fields.items())])

    def missing_names(self, said: str, first_names: frozenset[str]) -> list[str]:
        """Sözde geçen rehber adlarından LLM'in yazmadıkları (ölçüm 3, F03: "ahmet bey … ar-genin müdürü" → ad yok).
        Arayanın kendi adı ("ben can") sayılmaz."""
        named = fold_words(f"{self.ad} {self.soyad}")
        return [w for w in without_self(said) if w in first_names and not any(same_word(w, n) for n in named)]

    def repaired(self, said: str, first_names: frozenset[str], last_names: frozenset[str],
                 unit_said: Callable[[str], bool]) -> tuple[Understanding, list[str]]:
        """Sözle onarım (uydurma eklemeden): sözde geçen ama yazılmayan ad / soyad eklenir; kişi adı varken sözde
        geçmeyen birim atılır (ölçüm 4, F04: "selin hanım … ekinci" → ad selin, uydurma birim İK, soyad yok)."""
        got, notes = self, []
        if not got.ad and (missing := got.missing_names(said, first_names)):
            got = replace(got, ad=missing[0])
            notes.append(f"ad eklendi: {missing[0]}")
        named = fold_words(f"{got.ad} {got.soyad}")
        lasts = [w for w in without_self(said) if w in last_names and not any(same_word(w, n) for n in named)]
        if got.ad and not got.soyad and lasts:
            got = replace(got, soyad=lasts[0])
            notes.append(f"soyad eklendi: {lasts[0]}")
        if got.unvan and not title_said(got.unvan, said):
            got = replace(got, unvan="")
            notes.append(f"sözde olmayan unvan atıldı: {self.unvan}")
        if (got.ad or got.soyad) and got.birim and not unit_said(got.birim) and not grounded(got.birim, said):
            got = replace(got, birim="")
            notes.append(f"sözde olmayan birim atıldı: {self.birim}")
        return got, notes

    def problem(self, said: str, first_names: frozenset[str],
                unit_said: Callable[[str], bool] | None = None) -> str | None:
        """LLM'in yazdığı sözde yoksa (uydurma) ya da sözdeki bir adı atladıysa neden; tutarlıysa None.
        Unvan serbest: tarif ("başındaki kişi" → müdür) ve eş anlam ("sekreter" → asistan) LLM'in işi.
        Ölçüm 11.11.2026: "kreş" → uydurma "Eğitim ve Çocuk Bakımı" (R18); "kaliteden merve hanım" → ad atlandı
        (R10)."""
        words = fold_words(said)
        if self.birim and unit_said is not None and unit_said(self.birim):
            fields: tuple[str, ...] = (self.ad, self.soyad)   # birim eş anlamlısıyla söylendi ("personel" → İK)
        else:
            fields = (self.ad, self.soyad, self.birim)
        for field in fields:
            for w in fold_words(field):
                if w not in UNIT_NOUNS and not any(x.startswith(w) or same_word(w, x, CHOICE_WORD) for x in words):
                    return f"sözde yok: {w!r}"
        if self.dahili:
            heard = {*re.findall(r"\d{2,6}", said), *spoken_numbers(fold_words(said))}
            if not set(_DIGITS.findall(self.dahili)) <= heard:
                return f"dahili sözde yok: {self.dahili!r}"
        if self.ad and (missing := self.missing_names(said, first_names)):
            return f"sözdeki başka ad atlandı: {missing[0]!r}"   # LLM başka birini yazdı
        return None

    def canonical(self) -> str | None:
        """Kural adımlarının tanıdığı kalıp söz; belirsizse (ya da aktar niyetinde hiçbir alan yoksa) None."""
        if self.niyet in CANONICAL:
            return CANONICAL[self.niyet]
        if self.niyet != "aktar":
            return None
        if self.sira in ORDINAL_WORDS and not (self.ad or self.soyad or self.unvan or self.birim or self.dahili):
            return ORDINAL_WORDS[self.sira]
        if digits := _DIGITS.findall(self.dahili):
            return digits[0]
        # birim önce: "muhasebe müdür" → unvan kuralı, "muhasebe ayşe" → birim + ad kuralı
        # hitap korunur: "merve hanım" kişi kuralına gider, yalnız "merve" birim + ad sanılmasın (ölçüm R09)
        hitap = self.hitap if self.ad and not self.soyad else ""
        unvan = "" if self.ad and self.soyad else self.unvan   # tam ad yeter; uydurma unvan karıştırmasın (F04)
        parts = [self.birim, unvan, self.ad, self.soyad, hitap]
        text = " ".join(p for p in parts if p)
        return turkish_lower(text) or None


class UnderstandMixin:
    """İki giriş: `_understand_first` (kanıtı zayıf söz: adımlardan önce, `_handle_user`) ve `_understood_answer`
    (kuralların çözemediği söz: son adım, `_step_answer`). İkisi de en çok bir LLM çağrısı yapar (`u.asked`)."""

    _vocab_cache: tuple[int, int, frozenset[str], frozenset[str], frozenset[str]] | None = None

    def _vocabulary(self: DialogAgent) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
        """Rehberin sözcükleri (ad, soyad, birim, eş anlamlı, unvan; katlanmış) ve yalnız adlar. Rehber
        yenilenince (nesne ya da kayıt sayısı değişince) yeniden kurulur."""
        index = self.tools.index
        contacts = index.contacts()
        key = (id(index), len(contacts))
        if self._vocab_cache is None or self._vocab_cache[:2] != key:
            words: set[str] = set()
            firsts: set[str] = set()
            lasts: set[str] = set()
            for c in contacts:
                firsts.update(fold_words(c.first_name))
                lasts.update(fold_words(c.last_name))
                for t in (c.first_name, c.last_name, c.department, c.title, *c.department_aliases, *c.aliases):
                    words.update(fold_words(t))
            self._vocab_cache = (*key, frozenset(words), frozenset(firsts), frozenset(lasts))
        return self._vocab_cache[2], self._vocab_cache[3], self._vocab_cache[4]

    def unexplained(self: DialogAgent, text: str) -> list[str]:
        """Rehberle, istek kalıplarıyla ve dolgu sözcükleriyle açıklanamayan sözcükler ("artan sözcük"). Kural
        bu sözcükleri yok sayarak karar verir; çoksa kararın kanıtı zayıftır ("muhasebenin başındaki kişi")."""
        vocab = self._vocabulary()[0]
        known = FILLER_WORDS | YES_WORDS | NEGATION_WORDS | HONORIFICS | UNIT_NOUNS | PLACE_WORDS | OPERATOR_WORDS
        out = []
        for w in without_self(text):
            if (len(w) <= 2 or w.isdigit() or w in known or w in _GENERIC or w.startswith(_GENERIC_STEMS)
                    or w.startswith(_HONORIFIC_STEMS)
                    or spoken_numbers([w]) or w in vocab or any(same_word(w, v) for v in vocab)):
                continue
            out.append(w)
        return out

    def _weak_evidence(self: DialogAgent, u: _Utterance) -> bool:
        words = fold_words(u.text)
        return bool(NEGATION & set(words)) or len(self.unexplained(u.text)) >= COMPLEX_MIN

    async def _understand_first(self: DialogAgent, s: DialogSession, u: _Utterance) -> DialogTurn | None:
        """Kanıtı zayıf söz (ölçüm 2: F01, F07, F09, F17 kural yanlış ama emin): adımlardan önce LLM. Anlarsa kalıp
        sözle, anlamazsa özgün sözle adımlar sürer; son adımda ikinci LLM çağrısı yapılmaz."""
        if u.confirm or u.op_confirm or u.choice or not self._weak_evidence(u):
            return None
        if sound_alike(self.tools.index, u.text):
            return None    # bozuk duyulmuş ad ("bırakçayı" → Burak Çelik): kuralın ses teyidi (ölçüm 5, D124)
        log.info("[%s] anlama: kanıt zayıf, önce LLM (artan: %s)", s.call_id,
                 log_text(" ".join(self.unexplained(u.text))))
        return await self._understand(s, u, defer_guess=True)

    async def _understood_answer(self: DialogAgent, s: DialogSession, u: _Utterance) -> DialogTurn:
        if u.guess is not None:
            # önce LLM'e soruldu, cevap yalnız konudan tahmindi: kurallar bulamadıysa şimdi o teyitle sorulur
            if (turn := self._rule_if_found(s, u.text)) is not None:
                return turn
            g, u.guess = u.guess, None
            return self._topic_guess(s, u, g, 0) or self._rule_turn(s, u.text)
        if u.asked or not self.unexplained(u.text):
            return self._rule_turn(s, u.text)    # LLM'e zaten soruldu ya da anlatacak bir şey yok ("evet")
        if (turn := self._rule_if_found(s, u.text)) is not None:
            return turn                          # kural turu buldu (ölçüm 5, K01: LLM unvanı atıp birim hattı demişti)
        if (turn := await self._understand(s, u)) is not None:
            return turn                          # konudan birim tahmini: teyit sorusu
        if u.understood:
            return await self._run_steps(s, u)   # kalıp sözle adımlar yeniden; son adım kural turu (LLM yok)
        return self._rule_turn(s, u.text)        # LLM anlamadı / düştü: kural cevabı (bulunamadı, tekrar sor)

    def _rule_if_found(self: DialogAgent, s: DialogSession, text: str) -> DialogTurn | None:
        """Kural turu bir şey bulursa (aktarma, soru) onu döner; bulamazsa ("bulamadım") turun izleri (deneme sayacı,
        geçmiş) geri alınır ve None: söz LLM'e gider."""
        saved, hist = copy.deepcopy(s.state), len(s.history)
        turn = self._rule_turn(s, text)
        st = s.state
        if turn.actions or turn.ended or st.choice or st.confirm or st.confirm_operator:
            return turn
        s.state = saved          # miss kaydı da geri alınır: son adımdaki kural turu zaten kaydeder
        del s.history[hist:]
        return None

    async def _understand(self: DialogAgent, s: DialogSession, u: _Utterance,
                          defer_guess: bool = False) -> DialogTurn | None:
        """Sözü anla: kalıp söz `u.text`'e yazılır ve `u.understood` işaretlenir (adımlar onunla sürer); konudan birim
        tahmininde doğrudan teyit sorusu döner. LLM düşerse / belirsizse hiçbir şey değişmez (kural kipi)."""
        u.asked = True
        t0 = time.perf_counter()
        got = await self._ask_understanding(s, u.text)
        ms = (time.perf_counter() - t0) * 1000
        _, firsts, lasts = self._vocabulary()
        unit_said = self._unit_said(u.text)
        if got is not None and got.niyet == "aktar":
            got, notes = got.repaired(u.text, firsts, lasts, unit_said)
            if notes:
                log.info("[%s] anlama onarımı: %s", s.call_id, "; ".join(notes))
        if got is not None and got.niyet == "aktar" and (why := got.problem(u.text, firsts, unit_said)):
            log.info("[%s] anlama (LLM, %.0f ms): %s → %s → reddedildi (%s)", s.call_id, ms, log_text(u.text),
                     got.summary(), why)
            return None
        if got is None:
            log.info("[%s] anlama (LLM): sonuç yok (%.0f ms), kural kipi: %s", s.call_id, ms, log_text(u.text))
            return None
        target = got.ad or got.soyad or got.unvan or got.birim or got.dahili
        if got.niyet not in {"aktar", "konu", "bilgi"} and target:
            got = replace(got, niyet="aktar")    # ölçüm 6, D16: "ali veli lütfen" → selam + ad/soyad
        guess = None
        if got.niyet in {"konu", "bilgi"} and (got.tahmini_birim or got.birim):
            # ölçüm 3, F11: "iş başvurusu … öğrenmek istiyorum" → bilgi + birim İK: konu gibi teyitle
            guess = replace(got, tahmini_birim=got.tahmini_birim or got.birim)
        elif got.niyet == "konu" or (got.niyet == "aktar" and not target and got.tahmini_birim):
            guess = got                           # ölçüm 4, F09: niyet aktar, hedef yok, tahmini birim var
        if guess is not None:
            if defer_guess:
                # konudan tahmin en zayıf kanıt: önce kurallar (konu tablosu "arıza" → Teknik Destek, sözde açıkça
                # geçen birim "muhasebeyle"); bulamazlarsa son adımda bu tahmin sorulur (ölçüm 6: D53, D57, D65)
                log.info("[%s] anlama (LLM, %.0f ms): %s → %s → tahmin bekletildi", s.call_id, ms,
                         log_text(u.text), got.summary())
                u.guess = guess
                return None
            return self._topic_guess(s, u, guess, ms)
        if got.niyet == "aktar" and (turn := self._title_in_unit(s, got)) is not None:
            log.info("[%s] anlama (LLM, %.0f ms): %s → %s → birimde unvan", s.call_id, ms, log_text(u.text),
                     got.summary())
            return turn
        only_unit = got.niyet == "aktar" and got.birim and not (got.ad or got.soyad or got.unvan or got.dahili)
        if only_unit and (line := self._named_unit_line(got.birim)) is not None:
            # yalnız birim: kalıp söze çevirmeden birim hattı (ölçüm 4, F15: "yurt dışı satış" kalıpta bulunamadı)
            log.info("[%s] anlama (LLM, %.0f ms): %s → %s → birim hattı %s", s.call_id, ms, log_text(u.text),
                     got.summary(), line.extension)
            return self._code_transfer(s, Candidate(line, 100))
        canon = got.canonical()
        if canon is None:
            log.info("[%s] anlama (LLM, %.0f ms): %s → %s → belirsiz", s.call_id, ms, log_text(u.text),
                     got.summary())
            return None
        log.info("[%s] anlama (LLM, %.0f ms): %s → %s → %r", s.call_id, ms, log_text(u.text), got.summary(),
                 log_text(canon))
        u.text, u.understood = canon, True
        return None

    def _unit_said(self: DialogAgent, said: str) -> Callable[[str], bool]:
        """LLM'in yazdığı birim, sözde adıyla ya da eş anlamlısıyla geçiyor mu ("personelin müdürü" → İnsan
        Kaynakları; ölçüm 3, F08)."""
        words = fold_words(said)

        def said_as(birim: str) -> bool:
            key = search_key(birim)
            for c in self.tools.index.contacts():
                names = (c.department, *c.department_aliases)
                if key in {search_key(n) for n in names}:
                    return any(all(any(x.startswith(w) or same_word(w, x, CHOICE_WORD) for x in words)
                                   for w in fold_words(n) if w not in UNIT_NOUNS) for n in names)
            return False
        return said_as

    def _title_in_unit(self: DialogAgent, s: DialogSession, got: Understanding) -> DialogTurn | None:
        """Unvan + birim (ad yok): birimde (alt birimleri dahil) unvanı tutan hat doğrudan bulunur. Kalıp sözle
        kural yolu "müdür"ü Genel Müdürlük'le karıştırıyordu (ölçüm 3, F05: "müşteri hizmetleri müdür" → soru)."""
        if not (got.unvan and got.birim) or got.ad or got.soyad:
            return None
        key = search_key(got.birim)
        title = [TITLE_SYNONYMS.get(w, w) for w in fold_words(got.unvan) if len(w) > 2]
        if not title:
            return None
        found = []
        for c in self.tools.index.contacts():
            if c.kind == "department" or not c.transferable:
                continue
            units = {search_key(n) for n in (c.department, *c.department_aliases, *c.unit_path)}
            have = fold_words(c.title)
            if key in units and all(any(same_word(w, t) for t in have) for w in title):
                found.append(c)
        if len(found) == 1:
            return self._code_transfer(s, Candidate(found[0], 100))
        if 2 <= len(found) <= 3:
            cands = tuple(Candidate(c, 100) for c in found)
            return self._ask_titles(s, cands) or self._clarify(s, cands, lines=True)
        if not found and (line := self._named_unit_line(got.birim)) is not None:
            # unvan birimde yok: unvanı atıp birim hattına doğrudan aktarmak yerine sorulur (ölçüm 4, F02: "genel
            # müdürlük sekreter" kalıpta Genel Müdürlük hattına aktarılmıştı)
            asked = self._confirm(s, line, why="unvan birimde bulunamadı")
            s.state.confirm_suggested = True
            return asked
        return None

    def _named_unit_line(self: DialogAgent, birim: str) -> Any:
        """Birimin hattı: adıyla ya da eş anlamlısıyla; hat yoksa tek kişisi. Bulunamazsa None."""
        key = search_key(birim)
        if not key:
            return None
        pick, members = self._department_target(key)
        if pick is None:
            pick = next((c for c in self.tools.index.contacts() if c.kind == "department"
                         and key in {search_key(a) for a in (c.department, *c.department_aliases)}), None)
            pick = pick or (members[0].contact if len(members) == 1 else None)
        return pick

    def _topic_guess(self: DialogAgent, s: DialogSession, u: _Utterance, got: Understanding,
                     ms: float) -> DialogTurn | None:
        """Konu → tahmini birim: arayan söylemedi, her zaman teyitle ("Muhasebe'ye mi bağlamamı istersiniz?")."""
        if not search_key(got.tahmini_birim):
            log.info("[%s] anlama (LLM, %.0f ms): konu %r, birim yok: %s", s.call_id, ms, got.konu, log_text(u.text))
            return None
        pick = self._named_unit_line(got.tahmini_birim)
        if pick is None:
            log.info("[%s] anlama (LLM, %.0f ms): konu %r → %r rehberde yok", s.call_id, ms, got.konu,
                     got.tahmini_birim)
            return None
        log.info("[%s] anlama (LLM, %.0f ms): konu %r → %s (teyit)", s.call_id, ms, got.konu, pick.extension)
        asked = self._confirm(s, pick, why="konudan tahmin edilen birim")
        s.state.confirm_suggested = True
        return asked

    async def _ask_understanding(self: DialogAgent, s: DialogSession, text: str) -> Understanding | None:
        system = UNDERSTAND_PROMPT + self._understand_departments()
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}, {"role": "user", "content": text}]
        s.llm_calls += 1
        # JSON kipi ve kısa çıktı sınırı (Ollama `format: json`, `num_predict`); adaptör desteklemiyorsa düz sohbet
        try:
            llm: Any = self.llm          # chat_json LlmPort'ta zorunlu değil (ADR-037: adaptöre bağlı)
            if supports_json(llm):
                content, timing = await asyncio.wait_for(llm.chat_json(messages),
                                                         timeout=self.cfg.llm_timeout_s)
            else:
                content, timing = (await asyncio.wait_for(self.llm.chat(messages, []),
                                                          timeout=self.cfg.llm_timeout_s)).content, ""
        except (TimeoutError, LlmError) as e:
            log.warning("[%s] anlama (LLM) hatası: %s %s", s.call_id, type(e).__name__, e)
            return None
        if timing:
            log.info("[%s] anlama süresi: %s", s.call_id, timing)
        return Understanding.parse(content)

    def _understand_departments(self: DialogAgent) -> str:
        names = sorted({c.department for c in self.tools.index.contacts() if c.department}, key=turkish_lower)
        return ("\nBirimler: " + ", ".join(names) + ".") if names else ""
