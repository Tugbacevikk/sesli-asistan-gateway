"""Birden çok aday: "hangisi?" sorusu, birim yoluyla daraltma (ADR-032) ve cevaptan seçim."""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from sesli_asistan.libs.obs import log_text
from sesli_asistan.libs.text.normalizer import fold_tr_words

from .. import units
from ..models import Candidate, DialogTurn, Match
from ..people import (
    department_said,
    mentioned_targets,
    narrow_candidates,
    resolve_person,
    same_title,
)
from ..people.common import without_self
from ..people.names import name_said
from ..phrases import (
    FAST_BLOCK,
    NEGATION_WORDS,
    ORDINALS,
    YES_WORDS,
    ablative,
    contact_dative,
    question_particle,
    spoken_numbers,
    vowel_mask,
    wants_anyone,
    which_question,
)
from ..ports import ToolCall
from ..search import (
    fold_words,
    has_role_word,
    query_variants,
    search_key,
    similarity,
    strip_suffix,
)
from ..thresholds import (
    CHOICE_WORD,
    FIRST_NAME_LIKE,
    FUZZY_CHOICE_MARGIN,
    FUZZY_CHOICE_MIN,
)
from .common import (
    DialogSession,
    _assistant_tool_message,
    _choice_said,
    _common_title,
    _common_unit,
    _exact_title,
    _last_user,
    _spoken_title,
    _tool_message,
)

if TYPE_CHECKING:
    from .agent import DialogAgent

log = logging.getLogger("sesli_asistan.gateway.core.dialog")   # adı değişmez (log süzgeçleri)


class ChoiceMixin:
    def _narrow(self: DialogAgent, s: DialogSession, cands: tuple[Candidate, ...], text: str = "") -> DialogTurn | None:
        """Birden çok aday: 2–3 ve ayırt edilebiliyorsa "X mı, Y mi?"; birimleri ya da alt birimleri/unvanları
        farklıysa daraltma sorusu; aynı unvan ve aynı birimse operatör. None: normal akış."""
        if (turn := self._ask_units(s, cands)) is not None:
            return turn
        if len(cands) <= 3 and which_question([c.contact for c in cands]) is not None:
            if text and all(c.contact.kind == "person" for c in cands):
                return None                          # sorulabilir: kişi çözme / hızlı yol soracak
            s.state.truncated = False
            return self._clarify(s, cands, lines=True)
        depts = {search_key(c.contact.department) for c in cands}
        titles = {search_key(c.contact.title) for c in cands}
        if len(depts) >= 2 or len(titles) >= 2:
            ct, st = _common_title(cands), _spoken_title(text, cands)
            unvan = st if len(st.split()) > len(ct.split()) and ct and st.endswith(ct.split()[-1]) else (ct or st)
            unit = _common_unit(cands)
            if len(depts) >= 2:
                label = unvan or unit
                q = (self.cfg.texts.which_department.format(unvan=label) if label
                     else s.state.choice_question or self.cfg.texts.too_many)
            elif unvan:
                q = (f"{unit} bünyesinde hangi şubedeki {unvan}?" if unit
                     else self.cfg.texts.which_department.format(unvan=unvan))
            else:
                sub = any(w in fold_tr_words(unit) for w in ("sube", "subesi", "buro", "kisim"))
                q = (f"{unit} bünyesinde hangi görevli veya unvanla görüşmek istersiniz?" if sub
                     else f"{unit} bünyesinde hangi şube veya görevliyle görüşmek istersiniz?")
            log.info("[%s] aynı unvanlı %d hat (%s): birim soruluyor", s.call_id, len(cands), unvan or unit)
            s.state.choice, s.state.choice_question = cands, q
            turn = self._reply(s, q)
            turn.fast = True
            return turn
        return self._interchangeable(s, cands, text)

    # --- belirsiz aramada soru ve seçim (ADR-017) ---

    def _clarify(self: DialogAgent,
                 s: DialogSession, candidates: tuple[Candidate, ...], lines: bool = False) -> DialogTurn | None:
        """2–3 kişilik adaydan "Muhasebeden Ayşe Yılmaz mı, satıştan Ayşe Kaya mı?" sorusu (LLM'siz). Adaylar
        sorudan ayırt edilemiyorsa (aynı ad, aynı birim) None: LLM sorsun. `lines`: birim hatları da aday olabilir
        (sözde birden çok hedef: "Muhasebe mi, Teknik Destek mi?")."""
        if not s.state.truncated and (turn := self._ask_units(s, candidates)) is not None:
            return turn
        # kişi ve birim hattı karışık da sorulur ("Ayşe Kaya mı, Satış mı?"): canlı deneme 10.11.2026'da bozuk duyulan
        # "matış" ikisini buldu, soru LLM'e kaldı ("Matış isminde iki kişi var…", 6,6 sn)
        if not 2 <= len(candidates) <= 3 or (
                not lines and any(c.contact.kind not in ("person", "department") for c in candidates)):
            return None
        if s.state.truncated:
            return None                       # gösterilenden çok aday var: yalnız ilk üçünü sormak yanlış olur
        q = which_question([c.contact for c in candidates])
        if q is None:
            return None
        s.state.choice, s.state.choice_question = candidates, ""
        return self._reply(s, q)

    def _ask_titles(self: DialogAgent, s: DialogSession, cands: tuple[Candidate, ...]) -> DialogTurn | None:
        """Aynı birimde farklı unvanlı hatlar, bazıları aynı unvanda (ad yok, soru adla kurulamıyor): unvanla sorulur,
        "Hukuk Müşaviri mi, Avukat mı?" (S35). Seçenekte ortak unvan sözcüğü atılır ("Hukuk Müşaviri Avukat" →
        "Hukuk Müşaviri"); cevap tek hattı seçerse aktarılır, aynı unvanlı hatları seçerse ayırt edilemez (operatör)."""
        if len({c.contact.department for c in cands}) != 1:
            return None
        titles = list(dict.fromkeys(c.contact.title for c in cands if c.contact.title))
        if len(titles) < 2 or len(titles) == len(cands):
            return None                                     # hepsi farklıysa olağan "X mı, Y mi?" sorusu yeter
        # fold_words: unvan sözcükleri ad aramasında atılır ("avukat"), burada asıl sözcüktür
        common = set.intersection(*(set(fold_words(t)) for t in titles))
        labels = []
        for t in titles:
            rest = [w for w in t.split() if not set(fold_words(w)) <= common]
            labels.append(" ".join(rest) if rest else t)
        parts = [f"{x} {question_particle(x)}" for x in labels]
        q = ", ".join(parts[:-1]) + (", yoksa " if len(parts) > 2 else ", ") + parts[-1] + "?"
        s.state.choice, s.state.choice_question, s.state.truncated = cands, q, False
        log.info("[%s] aynı birimde %d unvan soruluyor: %s", s.call_id, len(titles), q)
        turn = self._reply(s, q)
        turn.fast = True
        return turn

    def _ask_units(self: DialogAgent, s: DialogSession, cands: tuple[Candidate, ...]) -> DialogTurn | None:
        """Adayların birim yolu varsa (ADR-032) yollarının ayrıldığı seviye sorulur: "Jandarma mı, Emniyet mi?".
        Sözde unvan varsa ("asayiş şube müdürü") birim hatları değil o birimlerin içindeki unvanlı hatlar sorulur:
        "müdürü" ~ "Müdürlüğü" benzerliğiyle Asayiş Şube Müdürlüklerinin birim hatları aday olabiliyor."""
        if all(c.contact.unit_path for c in cands):
            cands = self._titled_inside(s, cands)
        # tek aday kalırsa burada aktarılmaz: çağıran kendi akışına devam eder (_code_transfer → _topic_clash →
        # _clarify → buraya dönen bir döngü olmasın)
        contacts = [c.contact for c in cands]
        level = units.split_level(contacts)
        if level is None:
            return None
        q = units.question(units.groups(contacts, level), units.unit_names(self.tools.index.contacts()))
        s.state.choice, s.state.choice_question, s.state.truncated = cands, q, False
        log.info("[%s] birim yolu %d. seviye soruluyor (%d aday): %s", s.call_id, level + 1, len(cands), q)
        turn = self._reply(s, q)
        turn.fast = True
        return turn

    def _titled_inside(self: DialogAgent, s: DialogSession, cands: tuple[Candidate, ...]) -> tuple[Candidate, ...]:
        """Sözde unvan varken: birim hatlarını at (unvanlı hat varsa) ya da adaylar yalnız birim hatlarıysa o
        birimlerin içinde unvanı uyan hatlara geç. Değişiklik yoksa adaylar aynen döner."""
        text = _last_user(s)
        if not text or not has_role_word(text):
            return cands
        lines = [c for c in cands if c.contact.kind != "department"]
        if lines:
            return tuple(lines) if len(lines) < len(cands) else cands
        paths = [c.contact.unit_path for c in cands]
        inside = [c for c in same_title(self.tools.index, text)
                  if c.transferable and c.unit_path and any(c.unit_path[:len(p)] == p for p in paths)]
        return tuple(Candidate(c, 100) for c in inside) or cands

    def _resolve_units(self: DialogAgent,
                       s: DialogSession, text: str, choice: tuple[Candidate, ...]) -> DialogTurn | None:
        """Birim yolu sorusunun cevabı: adaylar cevaptaki birim adıyla süzülür (o seviyenin ya da daha aşağısının adı:
        "emniyet", "cinayet"). Tek aday → aktar; birden çok → sıradaki seviye; süzülemezse None (eski akış)."""
        contacts = [c.contact for c in choice]
        kept = units.narrow(contacts, text, units.unit_names(self.tools.index.contacts()))
        if not kept:
            return None
        log.info("[%s] birim yoluyla daraltıldı: %s → %s", s.call_id, log_text(text), [c.extension for c in kept])
        if len(kept) == 1:
            return self._code_transfer(s, Candidate(kept[0], 100))
        rest = tuple(Candidate(c, 100) for c in kept)
        if (turn := self._ask_units(s, rest)) is not None:
            return turn
        return self._narrow(s, rest) or self._clarify(s, rest, lines=True)

    def _fast_clarify(self: DialogAgent, s: DialogSession, text: str) -> DialogTurn | None:
        """Sözde yalnızca birden çok kişiye uyan bir ad var ("ayşe hanım"): LLM beklemeden sorulur. Sözde bir birim,
        unvan ya da olumsuzluk varsa LLM'e bırakılır (birimle birlikte arama gerekir)."""
        words = search_key(text).split()
        if not words or FAST_BLOCK & set(words) or any(strip_suffix(w) in FAST_BLOCK for w in words):
            return None
        if has_role_word(text) or self._mentions_department(words):
            return None
        index = self.tools.index
        if any(index.search(name=" ".join(words[i:i + 2])).match == Match.SINGLE_HIGH for i in range(len(words) - 1)):
            return None                                 # sözde bir tam ad da var ("Ayşe Yılmaz mı Ayşe Kaya mı")
        for n in (3, 2, 1):
            for i in range(len(words) - n + 1):
                window = " ".join(words[i:i + n])
                res = index.search(name=window)
                if res.match != Match.MULTIPLE or res.truncated or not all(
                        c.score >= index.high_score and c.contact.kind == "person" for c in res.candidates):
                    continue
                call = ToolCall(id=f"hizli_{len(s.history)}", name="InContacts", arguments={"Name": window})
                out = self.tools.execute(call, s.state)
                if out.content.get("match") != "multiple":
                    return None
                turn = self._clarify(s, s.state.last_results)
                if turn is None:
                    return None
                # LLM sonraki turda bağlamı görsün: araç çağrısı + sonucu sorudan önceye yazılır
                s.history[-1:-1] = [_assistant_tool_message("", [call]), _tool_message(call, out.content)]
                s.state.failed_attempts = 0
                turn.fast = True
                log.info("[%s] hızlı soru: %s → %s", s.call_id, log_text(text),
                         [c.contact.extension for c in s.state.choice])
                return turn
        return None

    def _resolve_choice(self: DialogAgent,
                        s: DialogSession, text: str, choice: tuple[Candidate, ...]) -> DialogTurn | None:
        """Sorulan adaylardan birini cevaptan seçer: birim ("muhasebedeki"), soyad ("Yılmaz"), tam ad ya da sıra
        ("ilki", "ikincisi"). Tek aday seçilmezse ya da arayan reddederse None: LLM devam eder."""
        words = search_key(text).split()
        raw = set(fold_tr_words(text))
        if raw & {"hicbiri", "hicbirisi", "hicbirine"} or ({"ikisi", "ucu"} & raw and raw & NEGATION_WORDS):
            # "hiçbiri", "ikisi de değil": sorulanlar arasında yok → kodla "bulamadım" (LLM turu beklenmez)
            if self._mentions_person_or_department(text):
                return None                            # "hiçbiri, Mehmet Demir'i arıyorum": LLM / arama
            return self._not_found(s)
        if wants_anyone(text) and not self._mentions_person(search_key(text).split()):
            # "kim müsaitse", "fark etmez": sorulanlardan birinciye, ulaşılamazsa sıradakine (adlı kişiler de).
            # Soru sözcüğü ("kim") denetiminden önce: o denetim sözü LLM'e bırakıyordu
            log.info("[%s] seçim: herhangi biri (%d aday)", s.call_id, len(choice))
            return self._transfer_in_order(s, choice, text)
        if not raw or FAST_BLOCK & raw or any(strip_suffix(w) in FAST_BLOCK for w in raw):
            return None
        if (turn := self._choice_by_title(s, text, choice)) is not None:
            return turn
        if any(c.contact.kind == "role" for c in choice) and not (set(words) & set(ORDINALS)):
            decided, turn = self._choice_by_unit(s, text, words, choice)
            if decided:
                return turn
        if not words:
            return None
        return self._choice_by_words(s, text, words, choice)

    def _choice_by_title(self: DialogAgent, s: DialogSession, text: str,
                         choice: tuple[Candidate, ...]) -> DialogTurn | None:
        """`_ask_titles` sorusunun cevabı ("Hukuk Müşaviri mi, Avukat mı?"): seçenek etiketi (unvanın ortak sözcük
        dışı kısmı) ya da yalnız ortak sözcük ("avukat") söylendiyse o unvanlı hat(lar). Tek hat → aktar; aynı unvanlı
        birden çok hat → ayırt edilemez, operatör. Soru bu biçimde değilse None. Sözcükler `fold_words` ile: unvan
        sözcükleri ("avukat") ad aramasında atılır, cevapta asıl sözcüktür (S35)."""
        titles = list(dict.fromkeys(c.contact.title for c in choice if c.contact.title))
        if len({c.contact.department for c in choice}) != 1 or len(titles) < 2 or len(titles) == len(choice):
            return None                                     # unvan sorusu değil (tek birim, tekrarlanan unvan)
        said = fold_words(text)

        def says(x: str) -> bool:
            return any(w.startswith(x) or similarity(strip_suffix(w), x) >= CHOICE_WORD for w in said)

        common = set.intersection(*(set(fold_words(t)) for t in titles))
        hit = [t for t in titles if (rest := set(fold_words(t)) - common) and all(says(x) for x in rest)]
        if not hit:
            hit = [t for t in titles if set(fold_words(t)) == common and all(says(x) for x in common)]
        if len(hit) != 1:
            return None
        picked = [c for c in choice if c.contact.title == hit[0]]
        if len(picked) == 1:
            return self._choice_transfer(s, picked[0], choice, text, "seçim (unvan)")
        return self._interchangeable(s, tuple(picked), text)

    def _interchangeable(self: DialogAgent, s: DialogSession, cands: tuple[Candidate, ...], text: str) -> DialogTurn:
        """Aynı unvanlı, aynı birimdeki **adsız** hatlar (iki "Avukat"): arayan da ayırt edemez, soru tur kaybettirir.
        Birinciye (dahili sırasıyla) aktarılır; meşgul / cevapsızsa santral sonucuyla sıradakine geçilir
        (`handle_transfer_result`). Hepsi bu çağrıda ulaşılamadıysa operatör. Karar 10.11.2026 (S35)."""
        named = [c for c in cands if c.contact.kind == "person" and c.contact.full_name.strip()]
        said = [c for c in named if name_said(c.contact, without_self(text))]
        if len(said) == 1 and (picked := self._choice_transfer(s, said[0], cands, text, "unvan + ad")) is not None:
            return picked                     # unvan + ad aynı sözde ("Ar-Ge mühendisi Volkan"): soru sorulmaz
        if named:
            # adı olan kişiler birbirinin yerine geçmez ("Ar-Ge Mühendisi": altı mühendis, adları belli): birimden
            # kiminle görüşüleceği sorulur, cevap adla seçilir. Önceden birinciye aktarılıyordu (deneme 10.11.2026)
            unit = cands[0].contact.department
            q = self.cfg.texts.ask_person_in_department.format(birimden=ablative(unit))
            log.info("[%s] aynı unvanlı %d kişi (%s): adı soruluyor", s.call_id, len(cands), cands[0].contact.title)
            s.state.choice, s.state.choice_question, s.state.truncated = cands, q, False
            ask = self._reply(s, q)
            ask.fast = True
            return ask
        return self._transfer_in_order(s, cands, text)

    def _transfer_in_order(self: DialogAgent, s: DialogSession, cands: tuple[Candidate, ...],
                           text: str) -> DialogTurn:
        """Adaylardan birinciye (dahili sırasıyla) aktar; meşgul / cevapsızsa `handle_transfer_result` sıradakine
        geçer. Hepsi bu çağrıda ulaşılamadıysa operatör."""
        order = sorted((c for c in cands if c.contact.extension not in s.state.unreachable),
                       key=lambda c: c.contact.extension)
        if not order:
            return self._operator(s, self.cfg.texts.indistinct, reason="ayirt_edilemedi")
        log.info("[%s] %d hat (%s): birinciye, kalanlar yedek", s.call_id, len(order),
                 order[0].contact.title or order[0].contact.department)
        turn = self._choice_transfer(s, order[0], cands, text, "sırayla denenen hat")
        if turn is None:
            return self._operator(s, self.cfg.texts.indistinct, reason="ayirt_edilemedi")
        s.state.alternates = tuple(order[1:])
        return turn

    def _choice_by_unit(self: DialogAgent, s: DialogSession, text: str, words: list[str],
                        choice: tuple[Candidate, ...]) -> tuple[bool, DialogTurn | None]:
        """Makam hatları: cevaptaki birim / şube / unvan sözcükleri adayları süzer. (True, tur): karar verildi (tur
        None ise LLM / yeni istek); (False, None): süzülmedi, sözcüklerle seçime geçilir."""
        choice_ids = {c.contact.id for c in choice}
        narrowed = narrow_candidates([c.contact for c in choice], text)
        # Cevap sorulan adaylardan birini süzüyorsa o seçilir; ancak süzmüyorsa ve rehberde başka bir hedefse
        # yeni istek sayılır. Sıra tersken "Hangi birimdeki nöbetçi amiri?" → "jandarma" kurumun santraline
        # aktarılıyordu (sentetik set S26, S43; CI'da kural modu 09.11.2026).
        if not narrowed:
            fast = self._fast_matches(text, words) if words else {}
            return (True, None) if fast and not (set(fast) & choice_ids) else (False, None)
        if len(narrowed) == 1:
            cand = Candidate(narrowed[0], 100)
            return True, self._choice_transfer(s, cand, (cand,), text, "seçim (daraltma)")
        if {c.id for c in narrowed} == choice_ids and (op := self._count_failure(s)) is not None:
            return True, op
        return True, self._narrow(s, tuple(Candidate(c, 100) for c in narrowed), text)

    def _choice_by_words(self: DialogAgent, s: DialogSession, text: str, words: list[str],
                         choice: tuple[Candidate, ...]) -> DialogTurn | None:
        """Ad, soyad, birim ya da sıra sözcüğüyle ("ikincisi") tek aday."""
        picked = {i for i in range(len(choice)) if _choice_said(choice, i, words)}
        for word, idx in ORDINALS.items():
            if word in words and -len(choice) <= idx < len(choice):
                picked.add(idx % len(choice))
        if not picked:
            return self._fuzzy_choice(s, text, words, choice)
        if len(picked) > 1 and (exact := _exact_title(choice, picked, words)) is not None:
            picked = {exact}                           # "daire başkanı" → Personel Daire Başkanı, şube müdürleri değil
        if len(picked) != 1:
            if len(choice) > 3 or s.state.choice_question:
                # "Hangi birimdeki müfettiş?" → "teftiş kurulundan": söylenen birimdekilerle yeniden (ADR-030)
                return self._narrow(s, tuple(choice[i] for i in sorted(picked)))
            return None
        return self._choice_transfer(s, choice[picked.pop()], choice, text, "seçim")

    def _choice_transfer(self: DialogAgent, s: DialogSession, cand: Candidate, results: tuple[Candidate, ...],
                         text: str, how: str) -> DialogTurn | None:
        s.state.last_results = results
        action = self.tools.transfer(cand.contact.extension, s.state)
        if action is None:
            return None
        s.state.failed_attempts = 0
        log.info("[%s] %s: %s → %s", s.call_id, how, log_text(text), cand.contact.extension)
        turn = self._reply(s, self._transfer_text(action.contact_id))
        turn.fast = True
        return turn

    def pending_question(self: DialogAgent, s: DialogSession) -> str | None:
        """Arayanın cevabını bekleyen soru (kodla sorulan), yoksa None."""
        texts = self.cfg.texts
        if s.state.confirm is not None:
            c = s.state.confirm.contact
            name, to = (c.full_name if c.kind != "department" else c.department), contact_dative(c)
            return texts.confirm.format(ad=name, ad_e=to, mi=question_particle(to))
        if s.state.confirm_operator:
            return texts.confirm_operator
        if s.state.choice:
            return s.state.choice_question or which_question([c.contact for c in s.state.choice])
        return None

    def concerns_call(self: DialogAgent, s: DialogSession, text: str) -> bool:
        """Söz bu çağrıyla ilgili mi: rehberde bir hedefi (kişi, birim, unvan, dahili) anıyor, açık "hangisi?"
        sorusunun adaylarından birini söylüyor ya da açık teyide evet / hayır diyor. Arka plan süzgeci (çağrı
        oturumu) yalnız ilgisiz kısık sözleri atar: canlı deneme 10.11.2026'da arayanın ilk sözünden 17 dB kısık
        gelen "satış birimi" cevabı (STT: "satış bizimi", "satış filmi") arka plan sayılıp iki kez atıldı."""
        ix, words = self.tools.index, fold_tr_words(text)
        if (s.state.confirm is not None or s.state.confirm_operator) and set(words) & (YES_WORDS | NEGATION_WORDS):
            return True
        if s.state.choice and (any(_choice_said(s.state.choice, i, words) for i in range(len(s.state.choice)))
                               or wants_anyone(text)):
            return True
        numbers = [*re.findall(r"\d{2,6}", text), *spoken_numbers(words)]
        return (department_said(ix, text) is not None or mentioned_targets(ix, text, numbers).count > 0
                or bool(resolve_person(ix, text)["adaylar"]))

    def _repeat_choice(self: DialogAgent, s: DialogSession, choice: tuple[Candidate, ...]) -> DialogTurn:
        """"Hangisi?" sorusunun cevabı anlaşılmadı (başka bir istek de yok): soru kodla yeniden sorulur, açık kalır.
        Canlı deneme 03.11.2026: anlaşılmayan cevap LLM'e gidince soru kapanıyor, sonraki "Yılmaz"a yakın cevap
        eşlenemiyordu. Deneme sayılır (sonunda operatör)."""
        if (turn := self._count_failure(s)) is not None:
            return turn
        q = s.state.choice_question or which_question([c.contact for c in choice])
        s.state.choice = choice
        turn = self._reply(s, q or self.cfg.texts.not_understood)
        turn.fast = True
        return turn

    def _fuzzy_choice(self: DialogAgent, s: DialogSession, text: str, words: list[str],
                      choice: tuple[Candidate, ...]) -> DialogTurn | None:
        """Cevap hiçbir adaya tam uymadı ama bir adayın soyadına açıkça daha yakın: o aday teyitle sorulur.
        Santral denemesi (02.11.2026, mihu): "Ayşe Kaya mı, Ayşe Yılmaz mı?" → "Ayşe Yılmaz" STT'de "her şey uymaz"
        (güven 0,41) LLM'e gitmiş, LLM'in sorusu da anlaşılmayınca çağrı dolanmıştı."""
        # adaylarda ortak ad ("Ayşe") ayırt etmez: puana girmez ("ayşe" ↔ "kaya" %60 Kaya'yı öne çıkarıyordu)
        firsts = {search_key(c.contact.first_name) for c in choice if c.contact.first_name}
        long_words = [w for w in words if len(w) >= 4 and not any(similarity(w, f) >= FIRST_NAME_LIKE for f in firsts)]

        def score(last: str) -> float:
            if not last:
                return 0.0
            sim = max((similarity(v, last) for w in long_words for v in query_variants(w)), default=0.0)
            # aynı sesle biten bozuk duyuş, ünlü uyumu dahil: "yılmaz" → "yaramaz", "duymaz", "yüzmez", "gülmez"
            # (mihu, canlı 03.11.2026)
            ending = any(len(last) >= 4 and vowel_mask(w)[-3:] == vowel_mask(last)[-3:] for w in long_words)
            return round(max(sim, FUZZY_CHOICE_MIN + 5 if ending else 0.0), 1)

        scores = [score(search_key(c.contact.last_name)) for c in choice]
        best = max(range(len(choice)), key=scores.__getitem__)
        rest = max((sc for i, sc in enumerate(scores) if i != best), default=0.0)
        if scores[best] < FUZZY_CHOICE_MIN or scores[best] - rest < FUZZY_CHOICE_MARGIN:
            return None
        s.state.last_results = choice
        log.info("[%s] seçim (yakın, teyitle): %s → %s", s.call_id, log_text(text), choice[best].contact.extension)
        return self._confirm(s, choice[best].contact)

    def _fast_clarify_of(self: DialogAgent, s: DialogSession, contacts: list[Any]) -> DialogTurn | None:
        s.state.truncated = False
        if (turn := self._clarify(s, tuple(Candidate(c, 100) for c in contacts))) is not None:
            turn.fast = True
        return turn
