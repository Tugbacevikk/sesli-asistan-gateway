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
from ..phrases import FILLER_WORDS, NEGATION_WORDS, OPERATOR_WORDS, YES_WORDS, spoken_numbers
from ..ports import LlmError
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
(çalışma saatleri), "farketmez" (kim olursa olsun), "belirsiz".
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
        parts = [self.birim, self.unvan, self.ad, self.soyad, hitap]
        text = " ".join(p for p in parts if p)
        return turkish_lower(text) or None


class UnderstandMixin:
    """İki giriş: `_understand_first` (kanıtı zayıf söz: adımlardan önce, `_handle_user`) ve `_understood_answer`
    (kuralların çözemediği söz: son adım, `_step_answer`). İkisi de en çok bir LLM çağrısı yapar (`u.asked`)."""

    _vocab_cache: tuple[int, int, frozenset[str], frozenset[str]] | None = None

    def _vocabulary(self: DialogAgent) -> tuple[frozenset[str], frozenset[str]]:
        """Rehberin sözcükleri (ad, soyad, birim, eş anlamlı, unvan; katlanmış) ve yalnız adlar. Rehber
        yenilenince (nesne ya da kayıt sayısı değişince) yeniden kurulur."""
        index = self.tools.index
        contacts = index.contacts()
        key = (id(index), len(contacts))
        if self._vocab_cache is None or self._vocab_cache[:2] != key:
            words: set[str] = set()
            firsts: set[str] = set()
            for c in contacts:
                firsts.update(fold_words(c.first_name))
                for t in (c.first_name, c.last_name, c.department, c.title, *c.department_aliases, *c.aliases):
                    words.update(fold_words(t))
            self._vocab_cache = (*key, frozenset(words), frozenset(firsts))
        return self._vocab_cache[2], self._vocab_cache[3]

    def unexplained(self: DialogAgent, text: str) -> list[str]:
        """Rehberle, istek kalıplarıyla ve dolgu sözcükleriyle açıklanamayan sözcükler ("artan sözcük"). Kural
        bu sözcükleri yok sayarak karar verir; çoksa kararın kanıtı zayıftır ("muhasebenin başındaki kişi")."""
        vocab, _ = self._vocabulary()
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
        log.info("[%s] anlama: kanıt zayıf, önce LLM (artan: %s)", s.call_id,
                 log_text(" ".join(self.unexplained(u.text))))
        return await self._understand(s, u)

    async def _understood_answer(self: DialogAgent, s: DialogSession, u: _Utterance) -> DialogTurn:
        if u.asked or not self.unexplained(u.text):
            return self._rule_turn(s, u.text)    # LLM'e zaten soruldu ya da anlatacak bir şey yok ("evet")
        if (turn := await self._understand(s, u)) is not None:
            return turn                          # konudan birim tahmini: teyit sorusu
        if u.understood:
            return await self._run_steps(s, u)   # kalıp sözle adımlar yeniden; son adım kural turu (LLM yok)
        return self._rule_turn(s, u.text)        # LLM anlamadı / düştü: kural cevabı (bulunamadı, tekrar sor)

    async def _understand(self: DialogAgent, s: DialogSession, u: _Utterance) -> DialogTurn | None:
        """Sözü anla: kalıp söz `u.text`'e yazılır ve `u.understood` işaretlenir (adımlar onunla sürer); konudan birim
        tahmininde doğrudan teyit sorusu döner. LLM düşerse / belirsizse hiçbir şey değişmez (kural kipi)."""
        u.asked = True
        t0 = time.perf_counter()
        got = await self._ask_understanding(s, u.text)
        ms = (time.perf_counter() - t0) * 1000
        firsts = self._vocabulary()[1]
        if got is not None and got.niyet == "aktar" and not got.ad and (missing := got.missing_names(u.text, firsts)):
            got = replace(got, ad=missing[0])     # sözde geçen ad eklenir (arayan söyledi, uydurma değil)
        if got is not None and got.niyet == "aktar" and (why := got.problem(u.text, firsts, self._unit_said(u.text))):
            log.info("[%s] anlama (LLM, %.0f ms): %s → %s → reddedildi (%s)", s.call_id, ms, log_text(u.text),
                     got.summary(), why)
            return None
        if got is None:
            log.info("[%s] anlama (LLM): sonuç yok (%.0f ms), kural kipi: %s", s.call_id, ms, log_text(u.text))
            return None
        if got.niyet in {"konu", "bilgi"} and (got.tahmini_birim or got.birim):
            # ölçüm 3, F11: "iş başvurusu … öğrenmek istiyorum" → bilgi + birim İK: konu gibi teyitle
            return self._topic_guess(s, u, replace(got, tahmini_birim=got.tahmini_birim or got.birim), ms)
        if got.niyet == "konu":
            return self._topic_guess(s, u, got, ms)
        if got.niyet == "aktar" and (turn := self._title_in_unit(s, got)) is not None:
            log.info("[%s] anlama (LLM, %.0f ms): %s → %s → birimde unvan", s.call_id, ms, log_text(u.text),
                     got.summary())
            return turn
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
        key, title = search_key(got.birim), [w for w in fold_words(got.unvan) if len(w) > 2]
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
        return None

    def _topic_guess(self: DialogAgent, s: DialogSession, u: _Utterance, got: Understanding,
                     ms: float) -> DialogTurn | None:
        """Konu → tahmini birim: arayan söylemedi, her zaman teyitle ("Muhasebe'ye mi bağlamamı istersiniz?")."""
        key = search_key(got.tahmini_birim)
        if not key:
            log.info("[%s] anlama (LLM, %.0f ms): konu %r, birim yok: %s", s.call_id, ms, got.konu, log_text(u.text))
            return None
        pick, members = self._department_target(key)
        if pick is None:
            alias = next((c for c in self.tools.index.contacts() if c.kind == "department"
                          and key in {search_key(a) for a in (c.department, *c.department_aliases)}), None)
            pick = alias or (members[0].contact if len(members) == 1 else None)
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
        json_chat = getattr(self.llm, "chat_json", None)
        try:
            if json_chat is not None:
                content, timing = await asyncio.wait_for(json_chat(messages), timeout=self.cfg.llm_timeout_s)
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
