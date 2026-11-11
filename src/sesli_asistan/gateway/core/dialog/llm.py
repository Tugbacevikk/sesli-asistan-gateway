"""LLM döngüsü: araç çağrıları, arama sonucuna kodla cevap, akışlı cevap ve geçmiş."""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from ..models import DialogTurn
from ..ports import LlmError, ToolCall
from ..resilience import supports_stream
from ..search import (
    search_key,
    similarity,
)
from ..thresholds import (
    MAX_BARGE_REPEATS,
    MAX_REPLY_SENTENCES,
)
from .common import (
    DialogSession,
    _assistant_tool_message,
    _last_user,
    _Run,
    _tool_message,
    clean_reply,
    complete_sentences,
    first_sentences,
)

if TYPE_CHECKING:
    from .agent import DialogAgent

log = logging.getLogger("sesli_asistan.gateway.core.dialog")   # adı değişmez (log süzgeçleri)


class LlmMixin:
    # --- LLM döngüsü ---

    async def _run(self: DialogAgent, s: DialogSession, caller_turn: bool = True) -> DialogTurn:
        """LLM döngüsü: en çok `max_tool_rounds` tur (cevap ya da araç çağrıları). caller_turn=False: santral
        olayından sonra (deneme sayacına girmez, araç verilmez)."""
        r = _Run(caller_turn)
        for _ in range(self.cfg.max_tool_rounds):
            msg = await self._llm_message(s, r)
            if isinstance(msg, DialogTurn):
                return msg
            if not msg.tool_calls:
                return self._llm_text(s, msg.content, r)
            s.history.append(_assistant_tool_message(msg.content, msg.tool_calls))
            for call in msg.tool_calls:
                if (turn := await self._tool_call(s, call, len(msg.tool_calls), r)) is not None:
                    return turn
            if r.stop:
                return self._reply(s, self.cfg.texts.to_operator)
        log.warning("[%s] araç turu sınırı aşıldı", s.call_id)
        return self._operator(s, self.cfg.texts.error, reason="tur_siniri")

    async def _llm_message(self: DialogAgent, s: DialogSession, r: _Run) -> Any:
        """Bir LLM turu; hata ya da zaman aşımında bu tur kural tabanlı (`llm_fallback`) ya da operatör (DialogTurn)."""
        # Santral olayından sonra LLM yalnızca konuşur: araç verilmez (ölçüm 26.10.2026, D24/D25: "araç çağırma"
        # istemine rağmen operatöre bağladı / aynı kişiye tekrar aktardı)
        tools = self.tools.schemas if r.caller_turn else []
        # akış: yalnız bu tur operatöre dönüşemeyecekse (söylenen cümle geri alınamaz); düşük güvende de akış yok:
        # LLM "aktarıyorum" derse teyide dönüşen aktarmayla çelişmesin
        may_stream = ((not r.caller_turn or s.state.failed_attempts + 1 < self.cfg.max_failed_attempts)
                      and not (r.caller_turn and self._needs_confirm(s)))
        try:
            return await asyncio.wait_for(self._chat(s, tools, may_stream), timeout=self.cfg.llm_timeout_s)
        except (TimeoutError, LlmError) as e:
            user = _last_user(s)
            if self.cfg.llm_fallback and r.caller_turn and user and not s.state.actions and not r.found:
                log.warning("[%s] LLM hatası (%s): bu tur kural tabanlı", s.call_id, e or type(e).__name__)
                return self._rule_turn(s, user)
            log.error("[%s] LLM hatası (%s): operatöre aktarılıyor", s.call_id, e or type(e).__name__)
            return self._operator(s, self.cfg.texts.error, reason="llm_hatasi")

    def _llm_text(self: DialogAgent, s: DialogSession, content: str, r: _Run) -> DialogTurn:
        """LLM araçsız cevap verdi. Aramadan cevap verdi ve ilerleme yoksa deneme sayılır."""
        text = first_sentences(clean_reply(content), MAX_REPLY_SENTENCES)
        if r.caller_turn and r.found and not s.state.actions and not self._names_result(s, text):
            # Rehber bir şey buldu ama LLM sonucu kullanmadan kendi sorusunu sordu ("Başka bir isim mi veya birim mi
            # arıyorsunuz?", canlı 09.11.2026): bulunmuş sayılmaz. Yoksa sayaç her turda sıfırlanır, arayan
            # operatör teklifine hiç ulaşmaz.
            log.info("[%s] LLM bulunanı kullanmadı: deneme sayılıyor", s.call_id)
            s.state.failed_attempts = r.prev_failed
            r.found = False
        if r.caller_turn and not r.found and not r.failed and not s.state.actions:
            if (turn := self._count_failure(s)) is not None:
                return turn
            if s.state.failed_attempts == self.cfg.max_failed_attempts - 1 and "operatör" not in text:
                text = f"{text} {self.cfg.texts.offer_operator_suffix}".strip()
        return self._reply(s, text)

    async def _tool_call(self: DialogAgent,
                         s: DialogSession, call: ToolCall, n_calls: int, r: _Run) -> DialogTurn | None:
        """LLM'in bir araç çağrısı: önce kod denetimleri (teyit, operatör izni), sonra araç; sonuca göre kodla
        cevap (bulamadım, "hangisi?", aktarma) ya da None (sonuç geçmişe yazıldı, LLM devam eder)."""
        if r.caller_turn and call.name == "TransferCall" and (turn := self._confirm_llm_transfer(s, call)) is not None:
            return turn
        if r.caller_turn and call.name == "ConnectOperator" and not self._operator_requested(s):
            # Canlı 04.11.2026: "başka birine mi, operatöre mi?" sorusuna "mesai saatleri ne" → LLM operatöre
            # bağladı. Operatör yalnız arayan isterse ya da teklife "evet" derse (kod denetler).
            log.warning("[%s] LLM operatöre bağlamak istedi ama arayan istemedi: reddedildi", s.call_id)
            s.history.append(_tool_message(call, {"ok": False, "error": "OPERATOR_NOT_REQUESTED",
                                                  "detail": "Arayan operatör istemedi; ne istediğini sor."}))
            return None
        out = await self.tools.execute_async(call, s.state)
        content = dict(out.content)
        if out.failed_search and r.caller_turn and not r.failed:
            if (turn := self._search_failed(s, call, content, n_calls, r)) is not None:
                return turn
        elif call.name == "InContacts" and not out.failed_search:
            if not r.found:
                r.prev_failed = s.state.failed_attempts
            r.found = True
            s.state.failed_attempts = 0
        if call.name == "InContacts" and (turn := self._search_result(s, call, content, n_calls, r)) is not None:
            return turn
        r.stop = r.stop or out.stop
        s.history.append(_tool_message(call, content))
        return None

    @staticmethod
    def _names_result(s: DialogSession, text: str) -> bool:
        """LLM'in cevabı bulunan adaylardan birini anıyor mu (adı, soyadı, unvanı ya da biriminden bir sözcük)?"""
        said = {w for w in search_key(text).split() if len(w) >= 3}
        for c in s.state.last_results:
            k = c.contact
            words = search_key(f"{k.first_name} {k.last_name} {k.title} {k.department}").split()
            if any(w in said or (len(w) >= 4 and any(x.startswith(w[:4]) and x[:5] == w[:5] for x in said))
                   for w in words if len(w) >= 3):
                return True
        return False

    def _search_failed(self: DialogAgent, s: DialogSession, call: ToolCall, content: dict[str, Any], n_calls: int,
                       r: _Run) -> DialogTurn | None:
        """Rehber araması bir şey bulamadı: deneme sayılır; sınırda operatör, tek çağrıda "bulamadım" kodla (ADR-019),
        sondan bir önceki denemede LLM'e operatör teklif notu."""
        r.failed = True
        s.state.failed_attempts += 1
        if s.state.failed_attempts >= self.cfg.max_failed_attempts:
            s.history.append(_tool_message(call, content))
            return self._operator(s, self.cfg.texts.to_operator, reason="bulunamadi")
        if self.cfg.code_replies and n_calls == 1:
            # "bulamadım" kodla; LLM'e ikinci tur yazdırılmaz
            s.history.append(_tool_message(call, content))
            s.state.failed_attempts -= 1            # _not_found sayacı kendisi artırır
            return self._not_found(s)
        if s.state.failed_attempts == self.cfg.max_failed_attempts - 1:
            content["note"] = self.cfg.texts.offer_operator_note
        return None

    def _search_result(self: DialogAgent, s: DialogSession, call: ToolCall, content: dict[str, Any], n_calls: int,
                       r: _Run) -> DialogTurn | None:
        """InContacts sonucu kodla: birden çok → "hangisi?" (ADR-017), tek ve net → aktarma (ADR-012; düşük STT
        güveninde teyit, sözde geçmeyen hedefte "bulamadım")."""
        match = content.get("match")
        if self.cfg.clarify_by_code and r.caller_turn and match == "multiple" and n_calls == 1:
            s.history.append(_tool_message(call, content))
            all_roles = bool(s.state.last_results) and all(c.contact.kind == "role" for c in s.state.last_results)
            if (turn := self._clarify(s, s.state.last_results, lines=all_roles)) is not None:
                return turn               # ikinci LLM turu beklenmez
            if all_roles:
                user = _last_user(s)
                return self._narrow(s, s.state.last_results, user)
            s.history.pop()
        if (self.cfg.clarify_by_code and r.caller_turn and match == "single_low" and n_calls == 1
                and len(s.state.last_results) == 1):
            # tek, kesin olmayan aday ("ahmet bey" → Mehmet Demir): teyit sorusunu kod kurar (LLM'in cümlesi bozuk
            # çıkıyordu); aday arayanın sözünde hiç geçmiyorsa teyit de yok, "bulamadım"
            near = s.state.last_results[0].contact
            if (turn := self._unreachable_again(s, near, call, content)) is not None:
                return turn
            if not self._grounded(s, near, last_only=True):
                s.history.append(_tool_message(call, {**content, "note": "Arayan bu kişiyi söylemedi."}))
                return self._not_found(s)
            s.history.append(_tool_message(call, content))
            return self._confirm(s, near, why="benzer ad (single_low)")
        if not (self.cfg.auto_transfer_single_high and match == "single_high"):
            return None
        contact = s.state.last_results[0].contact
        if r.caller_turn and (turn := self._unreachable_again(s, contact, call, content)) is not None:
            return turn
        if r.caller_turn and self._needs_confirm(s):
            if (turn := self._ungrounded(s, call, contact)) is not None:
                return turn
            s.history.append(_tool_message(call, content))
            return self._confirm(s, contact)
        if r.caller_turn and (missing := _missing_unit_words(call.arguments.get("Department", ""), contact)):
            # aranan birimin ayırt edici sözcükleri bulunan birimde yok ("jandarma sosyal tesis müdürlüğü" → yalnız
            # "jandarma"dan Jandarma santrali; deneme 10.11.2026): sormadan aktarılmaz, teyit sorulur
            s.history.append(_tool_message(call, {**content, "note": f"Rehberde yok: {' '.join(missing)}"}))
            return self._confirm(s, contact, why="birimin bir kısmı rehberde yok")
        action = self.tools.transfer(contact.extension, s.state)
        if action is None:
            return None
        content["transferred"] = True
        s.history.append(_tool_message(call, content))
        return self._reply(s, self._transfer_text(action.contact_id))

    # --- yardımcılar ---

    async def _chat(self: DialogAgent, s: DialogSession, tools: list[dict[str, Any]], may_stream: bool) -> Any:
        s.llm_calls += 1
        if not (may_stream and s.sink and self.cfg.stream_replies and supports_stream(self.llm)):
            return await self.llm.chat(self._messages(s), tools)
        buf, final, spoken = "", None, 0
        async for d in self.llm.chat_stream(self._messages(s), tools):  # type: ignore[attr-defined]  # supports_stream
            if d.message is not None:
                final = d.message
                break
            buf += d.text
            if "<think>" in buf and "</think>" not in buf:
                continue                                   # düşünme bloğu bitmeden konuşma
            done, buf = complete_sentences(buf)
            for sentence in done:
                if (text := clean_reply(sentence)) and spoken < MAX_REPLY_SENTENCES:
                    s.sink(text)
                    spoken += 1
        if final is None:
            raise LlmError("akış mesajsız bitti")
        return final

    def _messages(self: DialogAgent, s: DialogSession) -> list[dict[str, Any]]:
        """Sistem istemi + son `max_history` mesaj; kesim bir kullanıcı mesajında başlar
        (araç çağrısı ile sonucu birbirinden ayrılmasın)."""
        h = s.history
        if len(h) > self.cfg.max_history:
            start = len(h) - self.cfg.max_history
            while start < len(h) and h[start]["role"] != "user":
                start += 1
            h = h[start:]
        return [{"role": "system", "content": self.system_prompt}, *h]

    def _repeat_cut(self: DialogAgent, s: DialogSession) -> DialogTurn | None:
        """Söze girildi ama söz anlaşılmadı (çoğu kez ortam sesi): "anlayamadım" yerine yarıda kalan cümle yeniden;
        deneme sayılmaz. Canlı 04.11.2026: gürültü asistanı susturdu, ardından üst üste "Sizi anlayamadım". Gürültü
        sürerse sonsuz döngü olmasın: üst üste MAX_BARGE_REPEATS kez, sonra olağan "anlayamadım" ve sayaç."""
        cut = self._last_assistant(s)
        if not cut or s.state.barge_repeats >= MAX_BARGE_REPEATS:
            s.state.barge_repeats = 0
            return None
        s.state.barge_repeats += 1
        log.info("[%s] söze girme anlaşılmadı: yarıda kalan cümle yineleniyor", s.call_id)
        s.state.actions = []
        reply = self._reply(s, cut)
        reply.fast = True
        return reply

    @staticmethod
    def _last_assistant(s: DialogSession) -> str:
        return next((m.get("content") or "" for m in reversed(s.history) if m["role"] == "assistant"
                     and (m.get("content") or "").strip()), "")


# birim adlarında ayırt edici olmayan sözcükler (katlanmış): bunlar eksik diye teyit sorulmaz. search.UNIT_NOUNS'tan
# dar: "tesis", "servis", "kurul" burada ayırt edicidir ("jandarma sosyal tesis müdürlüğü" ≠ Jandarma santrali).
_GENERIC_UNIT_WORDS = {"mudurlugu", "mudurluk", "mudurlugune", "baskanligi", "baskanlik", "komutanligi", "komutanlik",
                       "sube", "subesi", "birim", "birimi", "buro", "burosu", "daire", "dairesi", "il", "ilce", "genel",
                       "merkez", "merkezi", "departman", "departmani", "bolum", "bolumu"}


def _missing_unit_words(query: str, contact: Any) -> list[str]:
    """LLM'in birim aramasındaki ayırt edici sözcüklerden bulunan kayıtta (birim adı, eş anlamlıları, unvan) hiç
    geçmeyenler. Boşsa aranan birim bulunmuştur. Yalnız arama bir birim adıysa ("… müdürlüğü", "… şubesi"): konu
    sözcükleri ("arıza kaydı" → Teknik Destek) eksik sayılmaz."""
    every = search_key(query).split()
    if not set(every) & _GENERIC_UNIT_WORDS - {"il", "ilce", "genel", "merkez", "merkezi"}:
        return []
    words = [w for w in every if len(w) >= 3 and w not in _GENERIC_UNIT_WORDS]
    have = search_key(" ".join([contact.department, *contact.department_aliases, contact.title])).split()
    return [w for w in words
            if not any(x.startswith(w[:5]) or w.startswith(x[:5]) or similarity(w, x) >= 80 for x in have if x)]

