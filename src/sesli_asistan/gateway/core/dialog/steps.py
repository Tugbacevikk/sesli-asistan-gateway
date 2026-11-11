"""Akışın adımları (`STEPS`): her söz sırayla adımlardan geçer, ilk cevap veren turu bitirir."""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from sesli_asistan.libs.obs import log_text
from sesli_asistan.libs.text.normalizer import fold_tr_words

from .. import units
from ..models import Candidate, DialogTurn, HangupAction
from ..people import (
    department_said,
    fuzzy_departments,
    has_honorific,
    mentioned_targets,
    narrow_candidates,
    resolve_person,
    same_title,
    same_unit,
)
from ..phrases import (
    FAST_BLOCK,
    NEGATION_WORDS,
    ablative,
    asks_general_help,
    asks_office_hours,
    asks_operator,
    greeting_echo,
    is_greeting_only,
    is_small_talk,
    maybe_operator,
    spoken_numbers,
    unknown_target,
    wants_hangup,
)
from ..search import (
    has_role_word,
    is_role_word,
    search_key,
    strip_suffix,
)
from .common import (
    DialogSession,
    _ancestor_line,
    _Utterance,
)

if TYPE_CHECKING:
    from .agent import DialogAgent

log = logging.getLogger("sesli_asistan.gateway.core.dialog")   # adı değişmez (log süzgeçleri)


class StepsMixin:
    # --- adımlar (sıra: STEPS; her biri DialogTurn ya da None — None: sıradaki adım) ---

    async def _step_greeting(self: DialogAgent, s: DialogSession, u: _Utterance) -> DialogTurn | None:
        """"selam", "merhaba", "alo", "iyi günler": istek yok; "bulamadım" denmez, deneme sayılmaz, açık soru
        (teyit, "hangisi?") yeniden sorulur (canlı 03.11.2026)."""
        if not (is_greeting_only(u.text) or (is_greeting_only(u.text, noise=1) and not self._mentions_target(u.text))):
            return None
        if (self.cfg.small_talk and self.cfg.llm_enabled and is_small_talk(u.text)
                and self.pending_question(s) is None):
            return None             # hal hatır: LLM karşılık verip yönlendirir (ADR-035); açık soru varsa o yinelenir
        u.reopen(s)
        log.info("[%s] yalnız selam: %s", s.call_id, log_text(u.text))
        text = self.cfg.texts.greeting_reply
        if (echo := greeting_echo(u.text)) and text.startswith("Merhaba,"):
            text = f"{echo},{text[len('Merhaba,'):]}"     # "günaydın" → "Günaydın, kiminle…" (metin özelse dokunulmaz)
        reply = self._reply(s, self.pending_question(s) or text)
        reply.fast = True
        return reply

    async def _step_office_hours(self: DialogAgent, s: DialogSession, u: _Utterance) -> DialogTurn | None:
        """"saat kaça kadar açıksınız", "mesai saatleri ne": kodla cevap, açık soru yinelenir, deneme sayılmaz
        (canlı 04.11.2026: LLM'e gitti ve arayan istemeden operatöre bağlandı)."""
        if not asks_office_hours(u.text) or self._mentions_target(u.text):
            return None
        u.reopen(s)
        texts = self.cfg.texts
        hours = (texts.office_hours.format(saatler=self.cfg.office_hours) if self.cfg.office_hours
                 else texts.office_hours_unknown)
        log.info("[%s] mesai sorusu: %s", s.call_id, log_text(u.text))
        reply = self._reply(s, f"{hours} {self.pending_question(s) or texts.ask_target}")
        reply.fast = True
        return reply

    async def _step_general_help(self: DialogAgent, s: DialogSession, u: _Utterance) -> DialogTurn | None:
        """Hedefsiz genel istek ("bilgi almak istiyorum", "bir sorum var"): LLM'e gitmeden konu / kişi sorulur, deneme
        sayılmaz, açık soru yinelenir. Canlı 05.11.2026: LLM "bilgi"yi birim sanıp Bilgi İşlem'e aktarmıştı."""
        if not asks_general_help(u.text) or self._mentions_target(u.text) or self.tools.local_topic(u.text):
            return None
        u.reopen(s)
        log.info("[%s] genel istek: %s", s.call_id, log_text(u.text))
        reply = self._reply(s, self.pending_question(s) or self.cfg.texts.ask_topic)
        reply.fast = True
        return reply

    async def _step_confirm(self: DialogAgent, s: DialogSession, u: _Utterance) -> DialogTurn | None:
        """Açık teyit sorusunun ("X'e mi bağlamamı istersiniz?") cevabı."""
        if u.confirm is None:
            return None
        return self._resolve_confirm(s, u.text, u.confirm, u.suggested)

    async def _step_operator_confirm(self: DialogAgent, s: DialogSession, u: _Utterance) -> DialogTurn | None:
        """Açık "operatöre mi bağlamamı istersiniz?" sorusunun cevabı."""
        return self._resolve_operator_confirm(s, u.text) if u.op_confirm else None

    async def _step_asks_operator(self: DialogAgent, s: DialogSession, u: _Utterance) -> DialogTurn | None:
        """"operatöre bağlanmak istiyorum": LLM beklemeden (dizüstü ölçümde LLM turu 7–8 sn)."""
        if not (self.cfg.fast_path and asks_operator(u.text) and not self._mentions_person_or_department(u.text)):
            return None
        log.info("[%s] arayan operatör istedi: %s", s.call_id, log_text(u.text))
        return self._operator(s, self.cfg.texts.to_operator, reason="arayan_istedi")

    async def _step_hangup(self: DialogAgent, s: DialogSession, u: _Utterance) -> DialogTurn | None:
        """Kapatma isteği. Çok düşük güvenle yalnız kurallı kapatma sözü ("kapatın", "sonlandırmak istiyorum");
        canlı 05.11.2026: arkadaki boğuk ses "kapatıya geç" (0,31) duyuldu ve çağrı kapandı."""
        if not wants_hangup(u.text, strict=u.low) or self._mentions_target(u.text):
            return None
        log.info("[%s] arayan kapatmak istedi: %s", s.call_id, log_text(u.text))
        s.state.actions = []
        s.history.append({"role": "assistant", "content": self.cfg.texts.goodbye})
        return DialogTurn(self.cfg.texts.goodbye, [HangupAction("arayan_istedi")])

    async def _step_choice(self: DialogAgent, s: DialogSession, u: _Utterance) -> DialogTurn | None:
        """Açık "X mı, Y mi?" sorusunun cevabı; cevap değilse ve yeni bir istek de değilse soru yinelenir."""
        u.choice, s.state.choice = s.state.choice, ()
        if not (u.choice and self.cfg.clarify_by_code):
            return None
        if (turn := self._resolve_units(s, u.text, u.choice)) is not None:
            return turn
        if (turn := self._resolve_choice(s, u.text, u.choice)) is not None:
            return turn
        depts = {c.contact.department for c in u.choice}
        if (s.state.choice_question and len(depts) == 1 and (dept := depts.pop())
                and department_said(self.tools.index, u.text) is None):
            # "Muhasebeden kiminle?" → "ayşe hanım": cevap o birimdeki kişi olarak aranır ("muhasebe ayşe hanım")
            u.text = f"{dept} {u.text}"
            return None
        if not set(fold_tr_words(u.text)) & (NEGATION_WORDS | {"bilmiyorum"}) and not self._mentions_target(u.text):
            return self._repeat_choice(s, u.choice)
        return None

    def _title_only_lines(self: DialogAgent, text: str, numbers: list[str]) -> bool:
        """Sözde kesin bir unvan var ("bilgi işlem müdürü") ve unvan sözcükleri atılınca geriye tek hedef kalıyor:
        öbür "hedef" unvan sözünün bir birim adına benzemesinden ("müdürü" ~ Genel Müdürlük; diyalog seti R12)."""
        res = resolve_person(self.tools.index, text)
        if not (res["kesin"] and sum(a.get("neden") == "unvan" for a in res["adaylar"]) == 1):
            return False
        core = " ".join(w for w in search_key(text).split() if not is_role_word(w))
        return len(mentioned_targets(self.tools.index, core, numbers).contacts) <= 1

    async def _step_conflict(self: DialogAgent, s: DialogSession, u: _Utterance) -> DialogTurn | None:
        """Sözde birden çok hedef: hiçbirine aktarılmaz, operatöre bağlanmaz, kapatılmaz; sorulur ("X mı, Y mi?",
        tek hedefte teyit). Ölçüm 05.11.2026 (sesli set, arkada ikinci konuşan 10 dB kısık): STT iki sözü
        karıştırıyor ("iki yüz on dört numaralı dahili Mehmet Demir'e bağlar mısınız", "görüşmeyi sonlandırmak
        istiyorum insan kaynaklarıyla"); ilk bulunana aktarılıyordu (80 kayıtta 18 yanlış eylem). Hedef: rehberdeki
        kişi (tam adla), birim hattı, dahili; operatör / kapatma isteği yanında bir hedef de birden çok sayılır.
        Kişinin kendi birimi ya da dahilisi ayrı hedef değildir ("muhasebeden Ayşe Yılmaz")."""
        if not (self.cfg.fast_path and self.cfg.clarify_by_code):
            return None
        if s.state.choice and units.narrow([c.contact for c in s.state.choice], u.text,
                                           units.unit_names(self.tools.index.contacts())):
            return None                 # açık birim sorusunun cevabı ("emniyet"): seçimde süzülür (ADR-032)
        numbers = [*re.findall(r"\d{2,6}", u.text), *spoken_numbers(fold_tr_words(u.text))]
        m = mentioned_targets(self.tools.index, u.text, numbers)
        persons = [c for c in m.contacts if c.kind == "person"]
        lines = [c for c in m.contacts if c.kind == "department"]
        intent = asks_operator(u.text) or wants_hangup(u.text, strict=u.low)
        targets = list(m.contacts)
        inner = [c for c in m.contacts if not _ancestor_line(c, m.contacts)]
        if len(inner) < len(m.contacts) and len(inner) <= 1 and not m.others and not intent:
            return None                 # üst birim + içindeki hat ("valilik hukuk müşaviri"): tek hedef (ADR-032)
        if intent and not targets:
            # hattı olmayan birim ("insan kaynaklarıyla") ya da yalnız soyad ("operate eli şahin'e"): hedef sayılır
            targets = [c for d in m.departments if (c := self._department_target(search_key(d))[0]) is not None]
            targets += [c for c in m.surnames if c not in targets]
        fuzzy = [c for d in sorted(fuzzy_departments(self.tools.index, u.text) - {x.department for x in lines}
                                   - set(m.departments))
                 if (c := self._department_target(search_key(d))[0]) is not None]
        if lines and not persons and fuzzy:
            # birim + bozuk duyulan başka birim ("teknik destek masebeye bağlar mısınız"): ikisi de sorulur
            targets = [*lines, *(c for c in fuzzy if c not in lines)]
        elif not m.others and (topic := self._topic_conflict(u.text, [*persons, *lines], m.departments)):
            # söylenen kişi / birim ile konunun birimi farklı ("satış mı faturamla ilgili bir sorun var", "ayşe
            # yılmaz fiyat teklifi almak istiyoruz"): ikisi de sorulur
            targets = [*persons, *lines, *(c for c in topic if c not in lines)]
        elif not (len(persons) + len(m.others) >= 2 or (persons and (lines or m.departments)) or len(lines) >= 2
                  or (intent and (targets or m.departments)) or (m.others and (lines or m.departments))):
            return None
        if lines and not persons and not intent and self._title_only_lines(u.text, numbers):
            return None
        log.info("[%s] sözde birden çok hedef%s: %s", s.call_id, " (operatör / kapatma isteğiyle)" if intent else "",
                 ", ".join(c.full_name for c in (*m.contacts, *m.others)))
        if len(targets) == 1:         # tek hedef + operatör / kapatma ya da hattı olmayan başka birim
            return self._confirm(s, targets[0], why="sözde birden çok hedef")
        if not targets:
            return self._reply(s, self.cfg.texts.ask_target)
        if len(persons) == 1 and not m.others and not intent and len(targets) == len(m.contacts):
            # kişi + başka birim ("satıştan Burak Çelik"; Burak muhasebede; D29): kişi teyitle sorulur
            return self._confirm(s, persons[0], why="kişi başka birimle söylendi")
        s.state.truncated = False
        if (turn := self._clarify(s, tuple(Candidate(c, 100) for c in targets), lines=True)) is not None:
            turn.fast = True
            return turn
        return self._reply(s, self.cfg.texts.ask_target)

    def _topic_conflict(self: DialogAgent, text: str, lines: list[Any], depts: tuple[str, ...]) -> list[Any]:
        """Sözde bir kişi / birim ve başka bir birime giden konu geçiyorsa konunun hedefi (yerel konu listesiyle)."""
        said = {c.department for c in lines} | set(depts)
        if not said:
            return []
        topic = self.tools.local_topic(text)
        if not topic or {c.contact.department for c in topic} & said:
            return []
        pick, _ = self._department_target(search_key(topic[0].contact.department))
        return [pick or topic[0].contact]

    async def _step_department_who(self: DialogAgent, s: DialogSession, u: _Utterance) -> DialogTurn | None:
        """Birim belli, kişi belli değil: "Muhasebeden kiminle görüşmek istersiniz?" ve cevap o birimin kişileri
        arasından seçilir. İki durum: (1) birim + rehberde olmayan ad + hitap ("muhasebe naşa hanım": birim hattına
        bağlanıyordu; arayan bir kişi istiyor), (2) yalnız birim söylendi ama birimin genel hattı yok ve birden çok
        kişi / makam var ("personel başkanlığı": "bulamadım" deniyordu)."""
        if not (self.cfg.fast_path and self.cfg.clarify_by_code):
            return None
        ix = self.tools.index
        dept = department_said(ix, u.text)
        if dept is None:
            return None
        key = search_key(dept)
        members = [c for c in ix.contacts() if c.kind != "department" and search_key(c.department) == key]
        has_line = any(c.kind == "department" and search_key(c.department) == key for c in ix.contacts())
        nobody = not resolve_person(ix, u.text)["adaylar"] and not self._mentions_person(search_key(u.text).split())
        unknown_person = nobody and has_honorific(u.text)
        only_department = nobody and not has_line and len(members) >= 2 and not has_role_word(u.text)
        if not members or not (unknown_person or only_department):
            return None
        if unknown_person and (turn := self._count_failure(s)) is not None:
            return turn                               # aynı bilinmeyen ad tekrar tekrar: sonunda operatör
        q = self.cfg.texts.ask_person_in_department.format(birimden=ablative(dept))
        log.info("[%s] birim belli, kişi değil (%s): %s", s.call_id, dept,
                 "bilinmeyen ad" if unknown_person else "hattı yok")
        s.state.choice, s.state.choice_question = tuple(Candidate(c, 100) for c in members), q
        turn = self._reply(s, q)
        turn.fast = True
        return turn

    async def _step_fast_path(self: DialogAgent, s: DialogSession, u: _Utterance) -> DialogTurn | None:
        """1. rehber, hızlı yol (ADR-016): tam ad, birim hattı."""
        return self._fast_path(s, u.text) if self.cfg.fast_path else None

    async def _step_fast_clarify(self: DialogAgent, s: DialogSession, u: _Utterance) -> DialogTurn | None:
        """1. rehber: ad birden çok kişiye uyuyor → "hangisi?" (ADR-017)."""
        if not (self.cfg.fast_path and self.cfg.clarify_by_code):
            return None
        failed_before = s.state.failed_attempts
        if (turn := self._fast_clarify(s, u.text)) is None:
            return None
        if u.choice and {x.contact.id for x in s.state.choice} == {x.contact.id for x in u.choice}:
            # aynı soru yeniden: ilerleme yok, deneme sayılır (canlı 03.11.2026: "ayşe ırmak", "ayşe yüzmez"…
            # her seferinde aynı soru, sayaç sıfırlandığı için hiç operatöre gitmedi)
            s.state.failed_attempts = failed_before
            if (op := self._count_failure(s)) is not None:
                return op
        return turn

    async def _step_fast_extension(self: DialogAgent, s: DialogSession, u: _Utterance) -> DialogTurn | None:
        """1. rehber: söylenen dahili numara."""
        return self._fast_extension(s, u.text) if self.cfg.fast_path else None

    async def _step_same_title(self: DialogAgent, s: DialogSession, u: _Utterance) -> DialogTurn | None:
        """Sözdeki unvana birden çok hat uyuyor ve "X mı, Y mi?" ile sorulamıyor (3'ten çok ya da aynı adlı): birim
        sorulur ("Hangi birimdeki müfettiş?"), cevapla süzülür. Hepsi aynı birimde aynı unvansa ayırt edilemez:
        operatör (ADR-030; makam hatlarında ad yok, "Teftiş Kurulu Müfettişi" onlarca hat olabilir). Ayrıca arayan
        önce çok hatlı bir başkanlık / şube söylediyse o birimin adayları tutulur; önceki turlarda söylenen birim /
        şube bilgisiyle de daraltılır."""
        if not self.cfg.clarify_by_code:
            return None
        words = search_key(u.text).split()
        if FAST_BLOCK & set(words) or any(strip_suffix(w) in FAST_BLOCK for w in words):
            return None
        cands = [c for c in same_title(self.tools.index, u.text) if c.transferable]
        from_unit = False
        if not cands and not has_role_word(u.text) and not self._mentions_person(words):
            cands = same_unit(self.tools.index, u.text)
            from_unit = bool(cands)
        if (len(cands) >= 2 and (prior := self._prior_user_text(s))
                and (narrowed := narrow_candidates(cands, prior))):
            cands = narrowed
            if len(cands) == 1:
                log.info("[%s] önceki turlarla daraltıldı: %s → %s", s.call_id, log_text(u.text),
                         cands[0].extension)
                return self._code_transfer(s, Candidate(cands[0], 100))
        if len(cands) == 1 and from_unit:
            log.info("[%s] birimden tek makam: %s → %s", s.call_id, log_text(u.text), cands[0].extension)
            return self._code_transfer(s, Candidate(cands[0], 100))
        if len(cands) < 2:
            return None
        return self._narrow(s, tuple(Candidate(c, 100) for c in cands), u.text)

    def _prior_user_text(self: DialogAgent, s: DialogSession) -> str:
        """Bu çağrıda son aktarma / eylemden sonraki önceki arayan sözleri (çok turlu daraltma için)."""
        parts: list[str] = []
        for m in reversed(s.history[:-1]):
            role, content = m.get("role"), m.get("content") or ""
            if role == "tool" and '"transferred": true' in content:
                break
            if role == "user" and content.startswith("[Santral]"):
                break
            if role == "user" and content:
                parts.insert(0, content)
        return " ".join(parts)

    async def _step_mcp(self: DialogAgent, s: DialogSession, u: _Utterance) -> DialogTurn | None:
        """2. MCP: kişi çözme (unvan, birim + ad, ad + hitap; ADR-025) ve konu → birim (ADR-023). Ses benzerliği
        adayı burada kullanılmaz, `u.sound`'a yazılır (`_step_sound_alike`)."""
        if not self.cfg.mcp_step:
            return None
        turn, u.sound = await self._mcp_step(s, u.text)
        return turn

    async def _step_unknown_target(self: DialogAgent, s: DialogSession, u: _Utterance) -> DialogTurn | None:
        """Hedef anlaşılmadı ("afiyete bağlanmak istiyorum" = operatöre): operatör teyidi."""
        if (self.cfg.fast_path and (unknown_target(u.text) is not None or maybe_operator(u.text))
                and not spoken_numbers(fold_tr_words(u.text)) and not self._mentions_person_or_department(u.text)):
            return self._confirm_operator(s, u.text)
        return None

    async def _step_repeat_gate(self: DialogAgent, s: DialogSession, u: _Utterance) -> DialogTurn | None:
        """STT güveni tekrar eşiğinin altında: LLM'e gitmez, tekrar istenir (açık "hangisi?" yinelenir; söze girmede
        yarıda kalan cümle)."""
        if not u.low:
            return None
        log.info("[%s] STT güveni çok düşük (%.2f): tekrar istendi: %s", s.call_id, s.stt_confidence or 0,
                 log_text(u.text))
        if u.choice:
            return self._repeat_choice(s, u.choice)  # "hangisi?" sorusu yinelenir ve açık kalır
        if u.lenient and (again := self._repeat_cut(s)) is not None:
            return again
        return self._failure(s, count=not u.lenient)

    async def _step_sound_alike(self: DialogAgent, s: DialogSession, u: _Utterance) -> DialogTurn | None:
        """STT adı bozmuş olabilir ("ağlayamaz" → Ayşe Yılmaz): ses benzerliği, LLM'den hemen önce ve her zaman
        teyitle (ADR-026). Operatör kuralından ve tekrar kapısından sonra: "efsane" (= "operatöre") Elif Şahin'e
        benzetilmesin (D68)."""
        if not u.sound:
            return None
        log.info("[%s] ses benzerliği (MCP): %s → %s (%d)", s.call_id, log_text(u.text),
                 u.sound[0].contact.extension, u.sound[0].score)
        return self._confirm(s, u.sound[0].contact, why="ses benzerliği")

    async def _step_fuzzy_target(self: DialogAgent, s: DialogSession, u: _Utterance) -> DialogTurn | None:
        """STT birim adını ya da konu sözcüğünü bozmuş ("masebeye bağlar mısınız", "çifremi unuttum"): tek birime
        benziyorsa **teyitle** sorulur ("Muhasebe'ye mi bağlamamı istersiniz?"). Sesli set 05.11.2026: temiz seste
        kalan kaçırmaların çoğu bu. Benzerlik: uzun sözcükte (≥ 5 harf) yazım benzerliği FUZZY_TARGET_MIN ya da s / ş /
        ç karışması düzeltilmiş baş eşleşmesi."""
        if not self.cfg.fast_path or u.low:
            return None                      # çok düşük güven: tekrar kapısı ister
        depts = fuzzy_departments(self.tools.index, u.text, self.tools.topic_table())
        if len(depts) != 1:
            return None
        pick, members = self._department_target(search_key(next(iter(depts))))
        target = pick or (members[0].contact if len(members) == 1 else None)
        if target is None:
            return None
        log.info("[%s] bozuk birim / konu: %s → %s (teyit)", s.call_id, log_text(u.text), target.extension)
        asked = self._confirm(s, target, why="bozuk duyulan birim ya da konu")
        s.state.confirm_suggested = True
        return asked

    async def _step_answer(self: DialogAgent, s: DialogSession, u: _Utterance) -> DialogTurn | None:
        """3. LLM (ya da LLM kapalıyken kural modu, ADR-019): her zaman cevap verir."""
        if not self.cfg.llm_enabled or u.understood:   # anlaşılan söz (ADR-037): ikinci LLM turu yok
            return self._rule_turn(s, u.text)
        if self.cfg.understand == "llm" and not (u.confirm or u.op_confirm or u.choice):
            return await self._understood_answer(s, u)  # ADR-037: araçlı döngü yerine tek "anla" çağrısı
        return await self._run(s)
