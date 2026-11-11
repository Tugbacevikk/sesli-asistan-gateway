"""Aktarma ve teyit: kodla aktarma ve öncesindeki denetimler, teyit, operatör, bulunamadı, deneme sayacı,
santral sonucu."""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from sesli_asistan.libs.obs import log_text
from sesli_asistan.libs.text.normalizer import fold_tr_words, turkish_lower

from ..models import Candidate, DialogTurn, OperatorAction
from ..phrases import (
    NEGATION_WORDS,
    YES_WORDS,
    asks_operator,
    contact_dative,
    question_particle,
    spoken_numbers,
    wants_hangup,
)
from ..ports import ToolCall
from ..search import (
    query_variants,
    search_key,
    similarity,
)
from ..speech import split_sentences
from ..thresholds import (
    GROUNDED_MIN,
    NAME_SAME,
    YES_LIKE,
)
from .common import (
    DialogSession,
    _assistant_tool_message,
    _last_user,
    _tool_message,
)

if TYPE_CHECKING:
    from .agent import DialogAgent

log = logging.getLogger("sesli_asistan.gateway.core.dialog")   # adı değişmez (log süzgeçleri)


class TransferMixin:
    def _code_transfer(self: DialogAgent, s: DialogSession, cand: Candidate, confirmed: bool = False) -> DialogTurn:
        if not confirmed and (turn := self._deputy_guard(s, cand.contact)) is not None:
            return turn
        if not confirmed and (turn := self._fax_guard(s, cand.contact)) is not None:
            return turn
        if not confirmed and (turn := self._unreachable_again(s, cand.contact)) is not None:
            return turn
        if not confirmed and (turn := self._topic_clash(s, cand.contact)) is not None:
            return turn
        if not confirmed and self._unsure(s):
            return self._confirm(s, cand.contact)
        s.state.last_results = (cand,)
        action = self.tools.transfer(cand.contact.extension, s.state)
        if action is None:
            return self._not_found(s)
        s.state.failed_attempts = 0
        log.info("[%s] kural: → %s (%s)", s.call_id, cand.contact.extension, cand.contact.full_name)
        turn = self._reply(s, self._transfer_text(action.contact_id))
        turn.fast = True
        return turn

    def _deputy_guard(self: DialogAgent, s: DialogSession, contact: Any) -> DialogTurn | None:
        """Arayan asıl makamı istedi, bulunan hat yardımcısı: "il jandarma komutanına" → Komutan (aktarılamaz,
        rehberde görünmez) yerine "Komutan Yardımcısı"na aktarılıyordu (demo seti D16). Kurumun birim hattına
        (ADR-025) gidilir; o da yoksa teyit sorulur."""
        # yalnız kişisiz makam hattı: kişi adıyla bulunan "Vali Yardımcısı Kemal Bey" bu kurala girmez
        if contact.kind != "role" or not any(w.startswith("yardimci") for w in fold_tr_words(contact.title)):
            return None
        text = _last_user(s)
        if any(w.startswith("yardimci") for w in fold_tr_words(text)):
            return None
        log.info("[%s] istenen makam değil yardımcısı bulundu (%s): birim hattına", s.call_id, contact.title)
        pick = self._unit_line(contact)
        if pick is not None:
            return self._code_transfer(s, Candidate(pick, 100))
        return self._confirm(s, contact, why="yardımcı hattı")

    def _fax_guard(self: DialogAgent, s: DialogSession, contact: Any) -> DialogTurn | None:
        """Belgegeçer / faks hattı istendi ("emniyet faks hattı"): o hat aktarılamaz (rehberde görünmez) ve söz birimin
        genel hattına sessizce aktarılıyordu (demo seti D18). Cümle söylenir, birim hattı teyitle sorulur."""
        text = _last_user(s)
        if not any(w.startswith(("faks", "fax", "belgegecer", "belge gecer")) for w in fold_tr_words(text)):
            return None
        if any(w.startswith(("faks", "fax", "belgegecer")) for w in fold_tr_words(contact.title)):
            return None                 # aktarılabilir bir faks hattı varsa (kurum öyle tanımladıysa) ona gidilir
        log.info("[%s] belgegeçer hattı istendi: %s teyit ediliyor", s.call_id, contact.full_name)
        turn = self._confirm(s, contact, why="belgegeçer hattı")
        turn.text = f"{self.cfg.texts.fax_line} {turn.text}"
        if s.history and s.history[-1].get("role") == "assistant":
            s.history[-1]["content"] = turn.text
        return turn

    def _unit_line(self: DialogAgent, contact: Any) -> Any:
        """Kaydın en yakın üst birim hattı (birim yolu varsa yoldan, yoksa aynı birim adından)."""
        lines = [c for c in self.tools.index.contacts() if c.kind == "department" and c.transferable]
        path = contact.unit_path
        for n in range(len(path), 0, -1):
            for c in lines:
                if c.unit_path == path[:n]:
                    return c
        return next((c for c in lines if c.department == contact.department), None)

    def _unsure(self: DialogAgent, s: DialogSession) -> bool:
        c = s.stt_confidence
        return c is not None and 0 < self.cfg.confirm_below and c < self.cfg.confirm_below

    def _confirm(self: DialogAgent, s: DialogSession, contact: Any, why: str = "") -> DialogTurn:
        """Aktarmadan önce teyit: STT güveni düşük (santral denemesi 02.11.2026: rehberde olmayan "Tuğba Çevik" dar
        bantlı seste "burak çelik" duyuldu), yalnız adla söylendi ya da hedef konudan çıkarıldı (`why`)."""
        s.state.confirm = Candidate(contact, 100)
        name = contact.full_name if contact.kind != "department" else contact.department
        log.info("[%s] %s: %s teyit ediliyor", s.call_id,
                 why or f"düşük STT güveni ({s.stt_confidence or 0:.2f})", name)
        to = contact_dative(contact)
        turn = self._reply(s, self.cfg.texts.confirm.format(ad=name, ad_e=to, mi=question_particle(to)))
        turn.fast = True
        return turn

    def _resolve_confirm(self: DialogAgent, s: DialogSession, text: str, cand: Candidate,
                         suggested: bool = False) -> DialogTurn | None:
        """Teyit sorusunun cevabı: evet → aktar; "hayır, Y" → Y aranır; yeni istek (kişi, birim, operatör, kapatma) →
        normal akış; geri kalan her şey (hayır, anlaşılmayan kısa söz) → bulamadım. Açık "evet" olmadan aktarılmaz."""
        raw = set(fold_tr_words(text))
        if raw & NEGATION_WORDS or raw & {"yanlis", "degil"}:
            if not self._mentions_person_or_department(text):
                # öneri (konudan birim) reddedildi → "Peki, kiminle?"; yanlış duyulan ad → "adını tekrar söyler misiniz"
                return self._declined(s) if suggested else self._not_found(s)
            # "hayır, Elif Şahin": düzeltme — olumsuzluk atılıp söylenen ad hızlı yolla aranır (yoksa LLM)
            rest = " ".join(w for w in text.split() if fold_tr_words(w) and fold_tr_words(w)[0] not in
                            NEGATION_WORDS | {"yanlis", "degil"})
            return self._fast_path(s, rest) if self.cfg.fast_path and rest else None
        if asks_operator(text) or wants_hangup(text):
            return None                                # yeni bir istek ("operatöre bağlayın"): normal akış
        if raw & YES_WORDS or (len(raw) <= 2
                               and any(3 <= len(w) <= 6 and similarity(w, "evet") >= YES_LIKE for w in raw)):
            return self._code_transfer(s, cand, confirmed=True)
        if self._mentions_person_or_department(text):
            return None                                # başka bir ad / birim söyledi: normal akış
        # Açık bir "evet" yoksa aktarılmaz. Santral denemesi (02.11.2026): "hayır" → STT "hayat" (güven 0,41) LLM'e
        # gitmiş, 8,5 sn sonra "bulamadım" denmişti. Kısa ve anlaşılmayan cevap = hayır: kodla, beklemeden.
        return self._not_found(s)

    def _confirm_operator(self: DialogAgent, s: DialogSession, text: str) -> DialogTurn:
        """Tek, rehberde karşılığı olmayan bir hedefe bağlanmak istendi ("afiyete bağlanmak istiyorum"):
        büyük olasılıkla bozuk duyulan "operatöre". mihu dar bantta "operatöre"yi sık bozuyor (canlı 03.11.2026:
        "afiyete", "efsane", "batıya"; okumada "kafataya") ve metin benzerliği yakalamıyor: LLM yerine kodla sorulur."""
        s.state.confirm_operator = True
        log.info("[%s] hedef anlaşılmadı, operatör soruluyor: %s", s.call_id, log_text(text))
        turn = self._reply(s, self.cfg.texts.confirm_operator)
        turn.fast = True
        return turn

    def _resolve_operator_confirm(self: DialogAgent, s: DialogSession, text: str) -> DialogTurn | None:
        """Evet → operatör; hayır → bulamadım; başka bir istek (ad, birim, kapatma) → normal akış."""
        raw = set(fold_tr_words(text))
        if raw & YES_WORDS or (len(raw) <= 2
                               and any(3 <= len(w) <= 6 and similarity(w, "evet") >= YES_LIKE for w in raw)):
            return self._operator(s, self.cfg.texts.to_operator, reason="arayan_istedi")
        if raw & NEGATION_WORDS or raw & {"yanlis"}:
            if not self._mentions_person_or_department(text):
                return self._not_found(s)
            # "hayır, Elif Şahin": olumsuzluk atılıp ad hızlı yolla aranır (yoksa LLM)
            rest = " ".join(w for w in text.split() if fold_tr_words(w) and fold_tr_words(w)[0] not in
                            NEGATION_WORDS | {"yanlis"})
            return self._fast_path(s, rest) if rest else None
        return None

    def _declined(self: DialogAgent, s: DialogSession) -> DialogTurn:
        """Teyit sorusuna (öneriye) "hayır": arayan bir şey aramadı, öneriyi reddetti → "bulamadım" değil "Peki,
        kiminle…?". Döngü olmasın diye deneme sayılır; sondan bir önceki denemede operatör teklifi eklenir."""
        if (turn := self._count_failure(s)) is not None:
            return turn
        text = self.cfg.texts.declined
        if s.state.failed_attempts == self.cfg.max_failed_attempts - 1:
            text = f"{text} {self.cfg.texts.offer_operator_suffix}"
        return self._reply(s, text)

    def _not_found(self: DialogAgent, s: DialogSession) -> DialogTurn:
        """Kodla "bulamadım" (ADR-019): deneme sayılır; sondan bir önceki denemede operatör teklif edilir, sonda
        operatöre aktarılır."""
        if (turn := self._count_failure(s)) is not None:
            return turn
        last = s.state.failed_attempts == self.cfg.max_failed_attempts - 1
        turn = self._reply(s, self.cfg.texts.not_found_again if last else self.cfg.texts.not_found)
        turn.fallback = True
        return turn

    def _needs_confirm(self: DialogAgent, s: DialogSession) -> bool:
        """Bu sözle aktarmadan önce teyit gerekir: STT güveni düşük ve söz açık bir "evet" değil (LLM'in kendi sorduğu
        "doğru mu?"ya net "evet" teyit sayılır)."""
        if not self._unsure(s):
            return False
        user = _last_user(s)
        return not set(fold_tr_words(user)) & YES_WORDS

    def _confirm_llm_transfer(self: DialogAgent, s: DialogSession, call: ToolCall) -> DialogTurn | None:
        """LLM düşük güvenli sözle aktarmak istedi: aktarma yerine kodla teyit sorusu. Santral denemesi (02.11.2026):
        STT "ateşe girmem" (güven 0,31) duyduğu sözle LLM aktarmıştı. Ayrıca arayan bir numara söyleyip LLM başka bir
        dahiliye aktarmak isterse (kişinin adı sözde geçmiyorsa) teyit: ölçüm 04.11.2026, örnekli istemde "yüz numaraya
        bağlayın" → LLM 214'e aktardı; politika durduramaz (214 aktarılabilir)."""
        ext = call.arguments.get("Extension")
        cand = next((c for c in s.state.last_results if isinstance(ext, str) and c.contact.extension == ext.strip()),
                    None)
        if cand is None:
            return None                                # politika zaten reddedecek
        if (turn := self._unreachable_again(s, cand.contact, call)) is not None:
            return turn
        if self._needs_confirm(s) and (turn := self._ungrounded(s, call, cand.contact)) is not None:
            return turn
        if not self._needs_confirm(s) and not self._other_number(s, cand.contact):
            return None
        s.history.append(_tool_message(call, {"ok": False, "note": "Aktarmadan önce arayana teyit soruldu."}))
        return self._confirm(s, cand.contact)

    def _ungrounded(self: DialogAgent, s: DialogSession, call: ToolCall, contact: Any) -> DialogTurn | None:
        """Düşük güvenli sözle LLM'in seçtiği hedef sözde hiç geçmiyorsa teyit de sorulmaz, "bulamadım". Canlı
        05.11.2026: rehberde olmayan "Tuğba Çevik" → STT "çokağa çeviriyle" (0,6); LLM "Kaya" diye arayıp "Ayşe
        Kaya'ya mı bağlamamı istersiniz?" sordurdu."""
        if self._grounded(s, contact):
            return None
        log.info("[%s] LLM'in hedefi sözde geçmiyor (%s): bulunamadı", s.call_id, contact.extension)
        s.history.append(_tool_message(call, {"ok": False, "note": "Arayan bu kişiyi söylemedi; bulunamadı."}))
        s.state.last_results = ()
        return self._not_found(s)

    def _grounded(self: DialogAgent, s: DialogSession, contact: Any, last_only: bool = False) -> bool:
        """Arayanın bu çağrıdaki sözlerinde (`last_only`: yalnız son sözünde) hedefin bir izi var: ad, soyad, takma ad,
        unvan ya da birim sözcüğüne benzeyen (≥ GROUNDED_MIN) bir sözcük ya da dahili numarası. Yoksa LLM hedefi
        kendisi uydurmuştur (STT sözü bozduğunda arama sözcüğünü kendisi seçer)."""
        users = [m["content"] for m in s.history if m["role"] == "user" and m.get("content")]
        user = " ".join(users[-1:] if last_only else users)
        said = search_key(user).split()
        if contact.extension in {*re.findall(r"\d{2,6}", user), *spoken_numbers(fold_tr_words(user))}:
            return True
        names = (contact.first_name, contact.last_name, *contact.aliases, contact.title, contact.department,
                 *contact.department_aliases)
        keys = {k for n in names for k in search_key(n).split() if len(k) >= 3}
        return any(similarity(v, k) >= GROUNDED_MIN for w in said for v in query_variants(w) for k in keys)

    def _unreachable_again(self: DialogAgent, s: DialogSession, contact: Any, call: ToolCall | None = None,
                           content: dict[str, Any] | None = None) -> DialogTurn | None:
        """Bu çağrıda az önce ulaşılamayan dahiliye, arayan son sözünde onu yeniden söylemedikçe ("Can Bey'i tekrar
        dener misiniz") aktarılmaz; yine ulaşılamadığı söylenir. Canlı 05.11.2026: Can Öztürk'e ulaşılamadı,
        sonra "bilgi almak istiyorum" → LLM yeniden Can Öztürk'e aktardı."""
        if contact.extension not in s.state.unreachable or self._asked_again(s, contact):
            return None
        log.info("[%s] %s bu çağrıda ulaşılamamıştı, arayan yeniden istemedi: aktarılmadı", s.call_id,
                 contact.extension)
        if call is not None:
            s.history.append(_tool_message(call, {**(content or {}), "ok": False,
                                                  "note": "Bu kişiye az önce ulaşılamadı; yeniden aktarılmadı."}))
        s.state.actions = []
        text = self.cfg.texts.transfer_failed.format(ad=contact.full_name, ad_e=contact_dative(contact))
        turn = self._reply(s, text[0].upper() + text[1:])
        turn.fast = True
        return turn

    def _asked_again(self: DialogAgent, s: DialogSession, contact: Any) -> bool:
        """Arayan son sözünde bu kaydı **kendisiyle** yeniden istedi: kişide adı / soyadı / takma adı / unvanı, birim
        hattında birimin adı, ya da dahili numarası. Birim sözcüğü kişiyi yeniden istemek sayılmaz ("biraz bilgi
        lazım" ≠ Bilgi İşlem'deki Can Öztürk)."""
        user = _last_user(s)
        if contact.extension in {*re.findall(r"\d{2,6}", user), *spoken_numbers(fold_tr_words(user))}:
            return True
        names = ((contact.department, *contact.department_aliases) if contact.kind == "department"
                 else (contact.first_name, contact.last_name, *contact.aliases, contact.title))
        keys = {k for n in names for k in search_key(n).split() if len(k) >= 3}
        said = search_key(user).split()
        return any(similarity(v, k) >= NAME_SAME for w in said for v in query_variants(w) for k in keys)

    def _topic_clash(self: DialogAgent, s: DialogSession, contact: Any) -> DialogTurn | None:
        """Kod doğrudan aktarmadan önce: sözdeki konu başka bir birime gidiyorsa ikisi de sorulur ("zeynep … faturam
        ile ilgili bir sorun var" → "Zeynep Arslan mı, Muhasebe mi?"). Sesli set 05.11.2026: arka plandan gelen tek bir
        ad ya da bozuk birim adı ("teknik şifremi unuttum") konuyu ezip yanlış yere aktarıyordu (ADR-029)."""
        if not self.cfg.clarify_by_code:
            return None
        user = _last_user(s)
        topic = self.tools.local_topic(user)
        if not topic or contact.department in {c.contact.department for c in topic}:
            return None
        pick, _ = self._department_target(search_key(topic[0].contact.department))
        other = pick or topic[0].contact
        if other.id == contact.id:
            return None
        log.info("[%s] hedef (%s) ile konunun birimi (%s) farklı: sorulacak", s.call_id, contact.extension,
                 other.department)
        s.state.truncated = False
        turn = self._clarify(s, (Candidate(contact, 100), Candidate(other, 100)), lines=True)
        if turn is not None:
            turn.fast = True
        return turn

    def _fast_transfer(self: DialogAgent, s: DialogSession, text: str, contact: Any, kind: str) -> DialogTurn | None:
        if (turn := self._deputy_guard(s, contact)) is not None:
            return turn
        if (turn := self._fax_guard(s, contact)) is not None:
            return turn
        if (turn := self._unreachable_again(s, contact)) is not None:
            return turn
        if (turn := self._topic_clash(s, contact)) is not None:
            return turn
        args = {"Department": contact.department} if kind == "department" else {"Name": contact.full_name}
        call = ToolCall(id=f"hizli_{len(s.history)}", name="InContacts", arguments=args)
        out = self.tools.execute(call, s.state)
        if out.content.get("match") != "single_high":
            return None
        action = self.tools.transfer(contact.extension, s.state)
        if action is None:
            return None
        s.state.failed_attempts = 0
        s.history.append(_assistant_tool_message("", [call]))
        s.history.append(_tool_message(call, {**out.content, "transferred": True}))
        log.info("[%s] hızlı yol: %s → %s (%s)", s.call_id, log_text(text), contact.extension, contact.full_name)
        turn = self._reply(s, self._transfer_text(action.contact_id))
        turn.fast = True
        return turn

    async def handle_transfer_result(self: DialogAgent, s: DialogSession, status: str) -> DialogTurn:
        pending = s.state.pending_transfer
        s.state.pending_transfer = None
        if status == "answered":
            return DialogTurn("", ended=True)
        if pending is not None:
            s.state.unreachable.add(pending.extension)     # aynı çağrıda istenmeden yeniden aktarılmasın
        while s.state.alternates:
            # birbirinin yerine geçen hatlar (aynı unvan, aynı birim): sıradakine kendiliğinden geçilir (S35)
            nxt, s.state.alternates = s.state.alternates[0], s.state.alternates[1:]
            if nxt.contact.extension in s.state.unreachable:
                continue
            action = self.tools.transfer(nxt.contact.extension, s.state)
            if action is None:
                continue
            log.info("[%s] %s: sıradaki hat %s", s.call_id, status, nxt.contact.extension)
            title = nxt.contact.title or nxt.contact.department
            return self._reply(s, self.cfg.texts.next_line.format(unvan=title))
        reasons = {"busy": "meşgul", "no_answer": "cevap vermiyor", "failed": "aktarma başarısız oldu"}
        target = next((c.contact for c in s.state.last_results
                       if pending and c.contact.extension == pending.extension), None)
        who = target.full_name if target is not None else "aranan kişi"
        if self.cfg.code_replies or not self.cfg.llm_enabled:
            # ADR-019: sabit cümle; LLM sonraki turda bağlamı görür
            t = {"busy": self.cfg.texts.busy, "no_answer": self.cfg.texts.no_answer}.get(
                status, self.cfg.texts.transfer_failed)
            s.history.append({"role": "user", "content": f"[Santral] {who} {reasons.get(status, 'ulaşılamadı')}."})
            name = who if who != "aranan kişi" else "Aradığınız kişi"
            text = t.format(ad=name, ad_e=contact_dative(target) if target is not None else "Aradığınız kişiye")
            return self._reply(s, text[0].upper() + text[1:])
        s.history.append({"role": "user", "content": f"[Santral] {who} {reasons.get(status, 'ulaşılamadı')}. "
                          "Şimdi araç çağırma; arayana bildir ve başka birine mi yoksa operatöre mi "
                          "aktarılmak istediğini sor."})
        return await self._run(s, caller_turn=False)

    def _transfer_text(self: DialogAgent, contact_id: str) -> str:
        c = self.tools.index.get(contact_id)
        if c is None:
            return self.cfg.texts.to_operator
        if c.kind == "department":
            return self.cfg.texts.transferring_department.format(birim=c.department)
        return self.cfg.texts.transferring.format(ad=c.full_name, ad_e=contact_dative(c))

    def _count_failure(self: DialogAgent, s: DialogSession) -> DialogTurn | None:
        """Deneme sayacını artırır; sınıra ulaşıldıysa operatör turunu döner."""
        s.state.failed_attempts += 1
        if s.state.failed_attempts >= self.cfg.max_failed_attempts:
            return self._operator(s, self.cfg.texts.to_operator, reason="ilerleme_yok")
        return None

    def _failure(self: DialogAgent, s: DialogSession, count: bool = True) -> DialogTurn:
        """STT boş/anlamsız metin verdi: LLM'e gitmeden sabit cümle, deneme sayacı artar (söze girilen sözde artmaz)."""
        if not count:
            text = self.cfg.texts.not_understood
            s.history.append({"role": "assistant", "content": text})
            return DialogTurn(text, fallback=True)
        if (turn := self._count_failure(s)) is not None:
            return turn
        n, limit = s.state.failed_attempts, self.cfg.max_failed_attempts
        text = self.cfg.texts.offer_operator if n == limit - 1 else self.cfg.texts.not_understood
        if n < limit - 1 and (q := self.pending_question(s)):
            # açık soru varken anlaşılmayan söz: soru yinelenir, açık kalır ("Sizi anlayamadım. Ar-Ge'den kiminle…?");
            # genel cümle arayana neye cevap vereceğini söylemiyordu (deneme 10.11.2026)
            text = f"{split_sentences(self.cfg.texts.not_understood)[0]} {q}"
        s.history.append({"role": "assistant", "content": text})
        return DialogTurn(text, fallback=True)

    def _operator_requested(self: DialogAgent, s: DialogSession) -> bool:
        """Arayanın son sözü operatör istiyor ya da operatör teklif eden soruya açık "evet"."""
        users = [i for i, m in enumerate(s.history) if m["role"] == "user"]
        if not users:
            return False
        user = s.history[users[-1]]["content"]
        if asks_operator(user):
            return True
        before = next((m.get("content") or "" for m in reversed(s.history[:users[-1]]) if m["role"] == "assistant"),
                      "")
        return "operatör" in turkish_lower(before) and bool(set(fold_tr_words(user)) & YES_WORDS)

    def _operator(self: DialogAgent, s: DialogSession, text: str, reason: str) -> DialogTurn:
        s.state.actions = []
        s.history.append({"role": "assistant", "content": text})
        return DialogTurn(text, [OperatorAction(reason=reason)], fallback=True)
