"""Kural tabanlı çözme: hızlı yol, rehber taraması, birim / konu adımı, söz denetimleri."""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from sesli_asistan.libs.obs import log_text
from sesli_asistan.libs.text.normalizer import TR_FOLD, fold_tr_words, turkish_lower

from ..models import Candidate, Contact, DialogTurn, Match, SearchResult
from ..people import (
    resolve_person,
    said_surely,
    sound_alike,
)
from ..people.common import without_self
from ..phrases import (
    FAST_BLOCK,
    FILLER_WORDS,
    OPERATOR_WORDS,
    TOPIC_INTENT,
    asks_operator,
    spoken_numbers,
)
from ..ports import ToolCall
from ..search import (
    hard_stem,
    has_role_word,
    is_role_word,
    query_variants,
    search_key,
    similarity,
    strip_suffix,
    word_stems,
)
from ..thresholds import (
    NAME_SAME,
    NEAR_NAME_MIN,
)
from .common import (
    DialogSession,
    _last_user,
)

if TYPE_CHECKING:
    from .agent import DialogAgent

log = logging.getLogger("sesli_asistan.gateway.core.dialog")   # adı değişmez (log süzgeçleri)


class RulesMixin:
    # --- LLM'siz diyalog (ADR-019) ---

    def _rule_turn(self: DialogAgent, s: DialogSession, text: str) -> DialogTurn:
        """LLM kapalıyken: sözde operatör isteği, dahili numara, kişi / unvan / birim aranır. Tek aday → aktarma,
        2–3 kişi → soru, bulunamazsa tekrar sor (deneme sayacı; sonunda operatör)."""
        if asks_operator(text):
            return self._operator(s, self.cfg.texts.to_operator, reason="arayan_istedi")
        if (turn := self._rule_number(s, text) or self._rule_title(s, text)) is not None:
            return turn
        return self._rule_people(s, text)

    def _rule_number(self: DialogAgent, s: DialogSession, text: str) -> DialogTurn | None:
        """Sözde rehberdeki bir dahili ("214", "iki yüz on dört") → aktar."""
        for digits in [*re.findall(r"\d{2,6}", text), *spoken_numbers(fold_tr_words(text))]:
            if (r := self._code_search(s, {"Extension": digits})).match == Match.SINGLE_HIGH:
                return self._code_transfer(s, r.candidates[0])
        return None

    def _rule_title(self: DialogAgent, s: DialogSession, text: str) -> DialogTurn | None:
        """MCP adımı kapalıyken de unvan kuralları (ADR-025 / ADR-030: "özlük şube müdürü" → makam hattı)."""
        if self.cfg.mcp_step:
            return None
        res = resolve_person(self.tools.index, text)
        titled = [c for a in res["adaylar"] if a.get("neden") == "unvan"
                  if (c := self.tools.index.get(a["id"])) is not None]
        sure = res["kesin"]
        if len(titled) > 1 and (inside := self._in_said_unit(titled, text)):
            # unvan birden çok birimde ("müdür") ama birim de söylendi ("kaliteden sorumlu müdür"): o birime daralt
            # (ölçüm 7, F07: "Genel Müdürlük mü, Kalite mi?" soruluyordu). Birim + unvan: "birim + ad" kadar güçlü
            titled, sure = inside, len(inside) == 1
        if len(titled) == 1 and sure:
            return self._code_transfer(s, Candidate(titled[0], 100))
        if 2 <= len(titled) <= 3:
            cands = tuple(Candidate(c, 100) for c in titled)
            return self._ask_titles(s, cands) or self._clarify(s, cands, lines=True)
        return None

    def _in_said_unit(self: DialogAgent, cands: list[Contact], text: str) -> list[Contact]:
        """Adaylardan, sözde adı (ya da eş anlamlısı) geçen birimde olanlar; birim söylenmediyse ya da hiçbiri o
        birimde değilse boş liste (daraltma yapılmaz)."""
        core = " ".join(w for w in search_key(text).split() if not is_role_word(w))
        depts = self._rule_scan(without_self(core))[1]   # "müdür" Genel Müdürlük'ü birim saydırmasın
        if not depts:
            return []
        return [c for c in cands
                if depts & {search_key(n) for n in (c.department, *c.department_aliases, *c.unit_path)}]

    def _rule_people(self: DialogAgent, s: DialogSession, text: str) -> DialogTurn:
        """Sözdeki kişi / birim: tek aday → aktar (yalnız adla söylendiyse teyit), çok aday → soru, yoksa bulamadım."""
        words = without_self(text)            # "ben mehmet, …": arayanın kendi adı aday değil (diyalog seti D122)
        people, depts, more = self._rule_scan(words)
        if depts and (more or len(people) > 1) and (inside := self._named_people(words, depts)):
            # birim + ad, adaşı çok ("muhasebeden Merve Hanım", altı Merve): arama 3 adayla sınırlı, birimdeki Merve
            # o üçte olmayabilir; söylenen birimdeki adaşlar ayrıca aranır (diyalog seti R09)
            people, more = {c.contact.id: c for c in inside}, False
        if people and depts:
            narrowed = {k: c for k, c in people.items() if search_key(c.contact.department) in depts}
            if not narrowed and len(people) == 1:
                # söylenen birim kişinin birimi değil ("satıştan Burak Bey"; Burak Çelik muhasebede): aktarılmaz,
                # sorulur (diyalog seti D29: kural modunda birim yok sayılıp aktarılıyordu)
                return self._confirm(s, next(iter(people.values())).contact)
            people = narrowed or people
        cands = sorted(people.values(), key=lambda c: -c.score)
        if not cands and len(depts) == 1:
            pick, cands = self._department_target(next(iter(depts)))
            if pick is not None:
                return self._code_transfer(s, Candidate(pick, 100))
        if len(cands) == 1 and not more:
            if not said_surely(self.tools.index, cands[0].contact, text):
                return self._confirm(s, cands[0].contact, why="yalnız ad ya da başka bir adla söylendi")
            return self._code_transfer(s, cands[0])
        # adaşı çok, birim söylenmedi ("Volkan Bey", dört Volkan): birim yoluyla seviye seviye sorulur (ADR-032)
        if (more and not depts and len(every := self._named_people(words)) > 3
                and (turn := self._ask_units(s, every)) is not None):
            s.state.failed_attempts = 0
            return turn
        if len(cands) > 1 or more:
            s.state.last_results = tuple(cands[:3])
            s.state.truncated = more or len(cands) > 3
            if (turn := self._clarify(s, s.state.last_results)) is not None:
                s.state.failed_attempts = 0
                return turn
            s.state.choice = ()
            return self._reply(s, self.cfg.texts.too_many)
        if (near := self._near_name(text)) is not None:
            # rehberde tam uyan yok ama tek bir kişinin adına yakın ("ahmet bey" → Mehmet Demir, D14): teyitle
            return self._confirm(s, near, why="benzer ad")
        if (sound := sound_alike(self.tools.index, text)) and (near := self.tools.index.get(sound[0]["id"])):
            # STT adı bozdu ("bırakçayı" → Burak Çelik): ses benzerliğiyle, yalnız teyitle (ADR-025). Önceden yalnız
            # MCP adımında vardı; kural modunda "bulamadım" deniyordu (D124, D125 yalnız MCP_STEP açıkken geçiyordu)
            return self._confirm(s, near, why="ses benzerliği")
        s.state.misses.append(text)
        return self._not_found(s)

    def _named_people(self: DialogAgent, words: list[str], depts: set[str] | None = None) -> tuple[Candidate, ...]:
        """Sözdeki ad / soyad pencerelerine (2, 1 sözcük) uyan **bütün** kişiler; `depts` verilirse yalnız o
        birimlerdekiler (birim yolunun herhangi bir seviyesi de sayılır: "satıştan" → Yurt İçi Satış'taki kişi)."""
        index = self.tools.index
        found: dict[str, Candidate] = {}
        for n in (2, 1):
            for i in range(len(words) - n + 1):
                window = " ".join(words[i:i + n])
                if not self._mentions_person(words[i:i + n]):
                    continue
                for c in index.matches(window):
                    k = c.contact
                    units = {search_key(k.department), *map(search_key, k.unit_path)}
                    if k.kind == "person" and (depts is None or units & depts):
                        found.setdefault(k.id, c)
            if found:
                break
        return tuple(found.values())

    def _near_name(self: DialogAgent, text: str) -> Any:
        """Sözün 1–2 sözcüklük ad pencerelerinden yalnız bir kişiye düşük skorla (single_low) uyan; yoksa None.
        Birim, unvan ve dolgu sözcükleri pencereye girmez."""
        words = [w for w in search_key(text).split() if len(w) >= 3 and strip_suffix(w) not in FILLER_WORDS
                 and not has_role_word(w) and not self._mentions_department([w])]
        found: dict[str, Any] = {}
        for n in (2, 1):
            for i in range(len(words) - n + 1):
                res = self.tools.index.search(name=" ".join(words[i:i + n]))
                if (res.match == Match.SINGLE_LOW and res.candidates[0].contact.kind == "person"
                        and res.candidates[0].score >= NEAR_NAME_MIN):
                    found.setdefault(res.candidates[0].contact.id, res.candidates[0].contact)
            if found:
                break
        return next(iter(found.values())) if len(found) == 1 else None

    def _rule_scan(self: DialogAgent, words: list[str]) -> tuple[dict[str, Candidate], set[str], bool]:
        """Sözün 3, 2, 1 sözcüklük pencereleri rehberde: (kişiler, söylenen birimler, gösterilenden çok aday var mı)."""
        index = self.tools.index
        people: dict[str, Candidate] = {}
        depts: set[str] = set()
        short_keys = {k for c in index.contacts() for k in map(search_key, c.department_aliases) if len(k) < 3}
        more = False
        for n in (3, 2, 1):
            for i in range(len(words) - n + 1):
                window = " ".join(words[i:i + n])
                if n == 1 and ((len(window) < 3 and window not in short_keys) or strip_suffix(window) in FILLER_WORDS):
                    continue                  # kısa söz atlanır; birim kısaltması ("it", "ik") hariç
                dept_hits = [c for c in index.search(department=window).candidates if c.score >= index.high_score]
                depts |= {search_key(c.contact.department) for c in dept_hits}
                if dept_hits and not has_role_word(window):
                    continue          # "satış", "muhasebeden": birim adı; unvanında geçen kişiler aday sayılmaz
                if n == 1 and not self._mentions_person([window]):
                    # tek söz yalnız ad / soyad olarak kişi seçer; unvan ya da birim sözü ("insan" → İnsan Kaynakları
                    # Uzmanı, "sistem" → Sistem Yöneticisi) seçmez (diyalog seti D58, D99: yanlış kişiye aktarma)
                    continue
                res = index.search(name=window)
                more = more or res.truncated
                for c in res.candidates:
                    if c.score >= index.high_score and c.contact.kind == "person":
                        people.setdefault(c.contact.id, c)
        return people, depts, more

    def _department_target(self: DialogAgent, dept: str) -> tuple[Any, list[Candidate]]:
        """Yalnız birim söylendi: birimin hattı, yoksa tek kişisi (aktarılacak); birden çok kişi → adaylar."""
        index = self.tools.index
        line = [c for c in index.contacts() if c.kind == "department" and search_key(c.department) == dept]
        members = [c for c in index.contacts() if c.kind == "person" and search_key(c.department) == dept]
        pick = line or (members if len(members) == 1 else [])
        return (pick[0], []) if pick else (None, [Candidate(c, 100) for c in members])

    def _code_search(self: DialogAgent, s: DialogSession, args: dict[str, str]) -> SearchResult:
        call = ToolCall(id=f"kural_{len(s.history)}", name="InContacts", arguments=args)
        self.tools.execute(call, s.state)
        return SearchResult(s.state.last_results, Match.SINGLE_HIGH if len(s.state.last_results) == 1
                            else Match.MULTIPLE if s.state.last_results else Match.NONE)

    def _fast_extension(self: DialogAgent, s: DialogSession, text: str) -> DialogTurn | None:
        """Sözde rehberdeki bir dahili numara ("214", "iki yüz on dörde"): LLM beklemeden aktarılır (kural modundaki
        gibi; güven düşükse teyit). Canlı deneme 03.11.2026: aynı söz LLM'le 2,8 sn."""
        raw = fold_tr_words(text)
        for digits in [*re.findall(r"\d{2,6}", text), *spoken_numbers(raw)]:
            if (r := self._code_search(s, {"Extension": digits})).match == Match.SINGLE_HIGH:
                log.info("[%s] dahili numara: %s → %s", s.call_id, log_text(text), digits)
                return self._code_transfer(s, r.candidates[0])
        return None

    async def _mcp_step(self: DialogAgent,
                        s: DialogSession, text: str) -> tuple[DialogTurn | None, tuple[Candidate, ...]]:
        """Akışın 2. adımı (ADR-023, ADR-025): rehber (1. adım) kesin karar veremedi; LLM'den önce MCP'ye sorulur:
        önce kişi çözme (unvan → o makamdaki kişi; birim + ad → o birimdeki kişi: aktar; ad + hitap → tek kişi: teyit,
        birden çok: "hangisi?"), sonra konu. Olumsuzluk / soru sözcüğü varsa kişi çözme yapılmaz (LLM yorumlasın).
        İkinci değer ses benzerliğiyle aday: çağıran onu LLM'den hemen önce teyitle kullanır."""
        words = search_key(text).split()
        sound: tuple[Candidate, ...] = ()
        if words and not (FAST_BLOCK & set(words) or any(strip_suffix(w) in FAST_BLOCK for w in words)):
            cands, sure, sound = await self.tools.resolve_person(text)
            if len(cands) == 1:
                log.info("[%s] kişi (MCP): %s → %s%s", s.call_id, log_text(text), cands[0].contact.extension,
                         "" if sure else " (teyit)")
                return (self._code_transfer(s, cands[0]) if sure
                        else self._confirm(s, cands[0].contact, why="kişi kesin değil")), ()
            if len(cands) > 3 and (turn := self._ask_units(s, cands)) is not None:
                # adaşı çok, birim yolu var ("Volkan Bey"): birim sorulur (ADR-032)
                log.info("[%s] kişi (MCP): %s → %d aday, birim soruluyor", s.call_id, log_text(text), len(cands))
                turn.fast = True
                return turn, ()
            if 2 <= len(cands) <= 3:
                s.state.last_results, s.state.truncated = cands, False
                if (turn := self._ask_titles(s, cands) or self._clarify(s, cands)) is not None:
                    log.info("[%s] kişi (MCP): %s → soru %s", s.call_id, log_text(text),
                             [c.contact.extension for c in cands])
                    turn.fast = True
                    return turn, ()
            if not cands and (turn := self._role_department(s, text, words)) is not None:
                return turn, ()
        return await self._topic_step(s, text), sound

    def _role_department(self: DialogAgent, s: DialogSession, text: str, words: list[str]) -> DialogTurn | None:
        """Unvan söylendi ama rehberde aktarılabilir böyle bir kayıt yok ("valiyle görüşmek istiyorum": Vali
        aktarılamaz, rehberde görünmez) ve söz tek bir birimin adı ("valiyle" ~ Valilik): o birimin genel hattı
        (kalem; ADR-025). LLM'e bırakılınca aynı birimdeki başka unvanlar soruluyordu ("Ayşe Yıldız mı, Kemal Arslan
        mı?", ölçüm 06.11.2026 K14). Kural modu aynı sonuca birim eşleşmesiyle varıyor."""
        if not has_role_word(text) or self._mentions_person(words):
            return None
        _, depts, _ = self._rule_scan(words)
        if len(depts) != 1:
            return None
        pick, _ = self._department_target(next(iter(depts)))
        if pick is None or pick.kind != "department":
            return None
        log.info("[%s] unvan rehberde yok, birim hattı: %s → %s", s.call_id, log_text(text), pick.extension)
        return self._code_transfer(s, Candidate(pick, 100))

    async def _topic_step(self: DialogAgent, s: DialogSession, text: str) -> DialogTurn | None:
        """Akışın 2. adımı (ADR-023): rehber (1. adım) çözemedi; yerel MCP sunucusuna sözdeki konu sorulur
        ("faturamla ilgili" → Muhasebe, "arızam var" → Teknik Destek). Tek hedef → aktar (güven düşükse teyit), 2–3 kişi
        → "hangisi?"; konu yok, MCP yok ya da belirsiz → None (3. adım: LLM). Sözde bir kişi adı geçiyorsa atlanır:
        arayan kişiyi istiyor, konu yalnız gerekçe ("faturam için Ayşe Hanım'ı arıyorum")."""
        if self._mentions_person(search_key(text).split()):
            return None
        if not self._topic_intent(text):
            return None
        cands = await self.tools.find_topic(text)
        if len(cands) == 1:
            log.info("[%s] konu (MCP): %s → %s", s.call_id, log_text(text), cands[0].contact.extension)
            if self.cfg.topic_confirm:
                # hedef arayanın sözünden değil konudan çıkarıldı: "Muhasebe'ye mi bağlamamı istersiniz?"
                asked = self._confirm(s, cands[0].contact, why="konudan çıkarılan hedef")
                s.state.confirm_suggested = True
                return asked
            return self._code_transfer(s, cands[0])
        if 2 <= len(cands) <= 3:
            s.state.last_results, s.state.truncated = cands, False
            if (turn := self._clarify(s, cands)) is not None:
                log.info("[%s] konu (MCP): %s → soru %s", s.call_id, log_text(text),
                         [c.contact.extension for c in cands])
                turn.fast = True
                return turn
        return None

    def _topic_intent(self: DialogAgent, text: str) -> bool:
        """Konu adımına girilecek söz mü: tek sözcüklü bir kırıntı (arka plan sesi, yarım söz) konu sayılmaz; birden çok
        sözcük, bir niyet kökü ("arızam", "görüşebilir", "istiyorum") ya da bir birim adı ("ik") varsa girilir. Kökler
        ön ekle eşlenir: eşlemede ek atılmazsa "arızam var" / "ik ile görüşebilir miyim" (D06) konu adımını
        atlıyordu."""
        words = fold_tr_words(text)
        if len(words) >= 2:
            return True
        if not words:
            return False
        return words[0].startswith(TOPIC_INTENT) or self._mentions_department(search_key(text).split())

    def _mentions_department(self: DialogAgent, words: list[str], only: Any = None) -> bool:
        """Sözde bir birim adı geçiyor mu; `only` verilirse yalnız o kaydın birimi (ve eş anlamlıları)."""
        keys = (self.tools.index.department_keys if only is None else
                {k for k in (search_key(only.department), *map(search_key, only.department_aliases)) if k})
        said = [word_stems(w) for w in words]            # "valiliğe" ~ "Valilik" (k / ğ), "emniyete" ~ "Emniyet"

        def inside(key: str) -> bool:
            kw = [hard_stem(strip_suffix(w)) for w in key.split()]
            return bool(kw) and any(all(i + j < len(said) and k in said[i + j] for j, k in enumerate(kw))
                                    for i in range(len(said)))

        return any(inside(k) or any(w.startswith(k) for w in words if len(k) >= 4) for k in keys)

    def _other_number(self: DialogAgent, s: DialogSession, contact: Any) -> bool:
        """Arayanın son sözünde bir numara var ve bu kişinin dahilisi değil; kişinin adı ya da soyadı da geçmiyor."""
        user = _last_user(s)
        numbers = {*re.findall(r"\d{2,6}", user), *spoken_numbers(fold_tr_words(user))}
        if not numbers or contact.extension in numbers:
            return False
        said = set(search_key(user).split()) | {strip_suffix(w) for w in search_key(user).split()}
        own = set(search_key(f"{contact.first_name} {contact.last_name}").split())
        return not (own & said)

    # --- hızlı yol (ADR-016) ---

    def _other_surnames(self: DialogAgent, words: list[str], contact: Any) -> list[Any]:
        """Sözde, `contact`ın adı / soyadı dışında başka kişilerin soyadına uyan kelime varsa o kişiler. Yalnız soyad:
        arayanın kendini tanıtması ("ben Mehmet, Ayşe Yılmaz'ı arıyorum") yaygındır, soyad söylemesi değil."""
        own = set(search_key(f"{contact.first_name} {contact.last_name}").split())
        rest = [w for w in words if w not in own and strip_suffix(w) not in own and len(w) >= 3]
        if not rest:
            return []
        out: dict[str, Any] = {}
        for c in self.tools.index.contacts():
            if c.kind != "person" or c.id == contact.id or not c.transferable:
                continue
            last = search_key(c.last_name)
            if len(last) < 3 or last in own:
                continue
            for w in rest:
                if any(v == last or (len(v) >= 4 and similarity(v, last) >= NAME_SAME) for v in query_variants(w)):
                    out[c.id] = c
        return list(out.values())

    def _mentions_person_or_department(self: DialogAgent, text: str) -> bool:
        words = search_key(text).split()
        return bool(words) and (self._mentions_person(words) or self._mentions_department(words))

    def _mentions_target(self: DialogAgent, text: str) -> bool:
        """Sözde aranacak biri / bir birim / operatör / dahili numara var mı ("vazgeçtim, Mehmet Kaya'ya bağla")."""
        raw = turkish_lower(text).translate(TR_FOLD).split()
        if ({strip_suffix(w) for w in raw} | set(raw)) & OPERATOR_WORDS or any(ch.isdigit() for ch in text):
            return True
        words = search_key(text).split()
        return bool(words) and (self._mentions_person(words) or self._mentions_department(words))

    def _mentions_person(self: DialogAgent, words: list[str]) -> bool:
        """Sözde bir kişinin adı ya da soyadı geçiyor mu (birim hattına kendiliğinden gitmemek için)."""
        known = self.tools.index.person_words
        for w in words:
            for v in query_variants(w):
                if v in known or (len(v) >= 4 and any(similarity(v, k) >= NAME_SAME for k in known)):
                    return True
        return False

    def _fast_path(self: DialogAgent, s: DialogSession, text: str) -> DialogTurn | None:
        """Arayanın sözünde bir kişinin **tam adı** (ad + soyad) ya da bir **birimin genel hattı** tek ve kesin
        geçiyorsa LLM'e gitmeden aktarır: "Ayşe Yılmaz ile görüşmek istiyorum", "teknik destek lütfen". LLM çağrısı tur
        gecikmesinin %60'ı (ölçüm 27.10.2026). Şüphede (olumsuzluk, birden çok aday, yalnız ad, unvan, düşük skor)
        hiçbir şey yapmaz; LLM normal akışla devam eder. Aktarma yine TransferPolicy'den geçer."""
        words = search_key(text).split()
        if not words or FAST_BLOCK & set(words) or any(strip_suffix(w) in FAST_BLOCK for w in words):
            return None
        found = self._fast_matches(text, words)
        persons = [c for c, kind in found.values() if kind in ("name", "alias") and c.kind == "person"]
        if len(found) >= 2 and len(persons) == len(found) and self.cfg.clarify_by_code:
            # sözde birden çok kişinin tam adı geçiyor ("Ayşe Yılmaz mı Zeynep Arslan mı"): kodla sorulur
            log.info("[%s] birden çok tam ad: %s", s.call_id, ", ".join(c.full_name for c in persons))
            return self._fast_clarify_of(s, persons)
        if len(found) != 1:
            return None                                # hiç ya da birden çok aday: LLM karar versin
        contact, kind = next(iter(found.values()))
        if kind != "department" and (others := self._other_surnames(words, contact)):
            # Santral denemesi (02.11.2026): rehberde olmayan "Tuğba Çevik" → STT "kuba çelik elif şahin" → Elif Şahin'e
            # aktarılmıştı. Sözde başka bir kişinin soyadı da geçiyorsa emin olunamaz: aktarılmaz, sorulur.
            log.info("[%s] hızlı yol: %s yanında başka soyad (%s) — sorulacak", s.call_id, contact.full_name,
                     ", ".join(c.full_name for c in others))
            if self.cfg.clarify_by_code and len(others) <= 2:
                return self._fast_clarify_of(s, [*others, contact])
            return None
        if (kind != "department" and self._mentions_department(words)
                and not self._mentions_department(words, only=contact)):
            # söylenen birim kişinin birimi değil ("satıştan Burak Çelik"; Burak muhasebede): aktarılmaz, sorulur
            log.info("[%s] hızlı yol: %s başka birimle söylendi — sorulacak", s.call_id, contact.full_name)
            return self._confirm(s, contact)
        if self._unsure(s):
            return self._confirm(s, contact)
        return self._fast_transfer(s, text, contact, kind)

    def _fast_matches(self: DialogAgent, text: str, words: list[str]) -> dict[str, tuple[Any, str]]:
        """Hızlı yolun adayları: kimlik → (kayıt, "alias" | "name" | "department"). Kişi yalnız takma adının tam hâli
        ya da ad + soyadla; birim yalnız genel hattı ve sözde bir kişi ya da unvan yokken."""
        index = self.tools.index
        found: dict[str, tuple[Any, str]] = {}
        if (c := index.alias_match(text)) is not None and c.kind == "person":
            found[c.id] = (c, "alias")                # "Ayşe Abla": takma adın tam hâli tek kişiye ait
        for n in (3, 2, 1):
            for i in range(len(words) - n + 1):
                window = " ".join(words[i:i + n])
                for kind in ("name", "department") if n >= 2 else ("department",):  # yalnız ad / soyad: belirsiz
                    res = index.search(**{kind: window})
                    if res.match != Match.SINGLE_HIGH or res.candidates[0].score < self.cfg.fast_path_min_score:
                        continue
                    c = res.candidates[0].contact
                    if kind == "name" and not self._full_name_said(c, window):
                        continue                       # unvan ya da takma adla değil, gerçekten ad + soyadla
                    if kind == "department" and (c.kind != "department" or has_role_word(text)
                                                 or self._mentions_person(words)):
                        continue                       # "muhasebeden ayşe hanım": birim değil, oradaki bir kişi
                    found.setdefault(c.id, (c, kind))
        return found

    def _full_name_said(self: DialogAgent, c: Any, window: str) -> bool:
        full = search_key(c.full_name)
        return (c.kind == "person" and len(full.split()) >= 2
                and max(similarity(q, full) for q in query_variants(window)) >= self.cfg.fast_path_min_score)
