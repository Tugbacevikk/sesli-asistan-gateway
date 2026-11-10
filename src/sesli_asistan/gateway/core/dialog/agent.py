"""DialogAgent: diyaloğun giriş noktası; yöntemleri konu konu karışım sınıflarında (mixin)."""
from __future__ import annotations

import logging
from dataclasses import replace

from sesli_asistan.libs.text.normalizer import turkish_lower

from ..models import Action, DialogTurn, TransferAction
from ..phrases import (
    strip_fillers,
)
from ..ports import LlmPort
from ..tools import ToolRegistry
from .choice import ChoiceMixin
from .common import (
    _BRACKETS,
    DEPARTMENTS_HEAD,
    FEW_SHOT,
    STEPS,
    SYSTEM_PROMPT,
    DialogConfig,
    DialogSession,
    DialogTexts,
    _Utterance,
    fixed_prompts,
)
from .llm import LlmMixin
from .rules import RulesMixin
from .steps import StepsMixin
from .transfer import TransferMixin

log = logging.getLogger("sesli_asistan.gateway.core.dialog")   # adı değişmez (log süzgeçleri)


class DialogAgent(StepsMixin, ChoiceMixin, RulesMixin, TransferMixin, LlmMixin):
    def __init__(self, llm: LlmPort, tools: ToolRegistry, cfg: DialogConfig | None = None):
        self.llm, self.tools = llm, tools
        self.cfg = cfg or DialogConfig()
        org = self.cfg.organization.strip()
        base = (self.cfg.prompt_template or SYSTEM_PROMPT).format(kurum=f"{org} şirketinin" if org else "bir şirketin")
        self._prompt_base = base + (FEW_SHOT if self.cfg.few_shot else "")
        self._prompt: tuple[int, str] | None = None

    @property
    def system_prompt(self) -> str:
        """Sistem istemi: şablon (+ örnekler) + kurumun birim listesi (`LLM_PROMPT_DEPARTMENTS`). Liste rehberden
        çıkar ve yalnız rehber değişince değişir; sonda durur: istemin başı her çağrıda aynı kalır (Ollama önceki
        çağrının hesabını yeniden kullanır)."""
        n = self.cfg.prompt_departments
        if n <= 0:
            return self._prompt_base
        ix = self.tools.index
        if self._prompt is None or self._prompt[0] != ix.version:
            names = sorted({c.department for c in ix.contacts() if c.department}, key=turkish_lower)
            listed = ", ".join(names[:n]) + (", …" if len(names) > n else "")
            self._prompt = (ix.version, self._prompt_base + (DEPARTMENTS_HEAD + listed + "." if names else ""))
        return self._prompt[1]

    def new_session(self, call_id: str) -> DialogSession:
        return DialogSession(self, call_id)

    def fixed_prompts(self) -> dict[str, str]:
        return fixed_prompts(self.cfg)

    @property
    def texts(self) -> DialogTexts:
        return self.cfg.texts

    def set_texts(self, texts: DialogTexts) -> None:
        """Ayar ekranından: süren çağrılar yeni metinleri bir sonraki cümlede kullanır."""
        self.cfg = replace(self.cfg, texts=texts)

    # --- giriş noktaları ---

    async def handle_user(self, s: DialogSession, text: str) -> DialogTurn:
        s.llm_calls = 0
        turn = await self._handle_user(s, text)
        turn.llm_calls = s.llm_calls
        return turn

    async def _handle_user(self, s: DialogSession, text: str) -> DialogTurn:
        """Arayanın bir sözü: hazırlık, sonra adımlar sırayla (`STEPS`); ilk cevap veren adım turu bitirir."""
        text = _BRACKETS.sub(" ", text or "").strip()  # arayan "[Santral]" taklidi yapamasın
        if self.cfg.stt_corrections:                     # kurumun STT düzeltmeleri (ADR-033)
            text = self.cfg.stt_corrections.apply(text)
        only_fillers = bool(text) and not (text := strip_fillers(text))
        c = s.stt_confidence
        u = _Utterance(text=text, lenient=s.after_barge_in, confirm=s.state.confirm,
                       suggested=s.state.confirm_suggested, op_confirm=s.state.confirm_operator,
                       low=c is not None and 0 < c < self.cfg.repeat_below)
        # açık sorular bu sözle kapanır; cevaplamayan adımlar (selam, mesai) `u.reopen` ile geri açar
        s.after_barge_in = False
        s.state.confirm, s.state.confirm_suggested, s.state.confirm_operator = None, False, False
        if not text:
            return self._empty_turn(s, u, only_fillers)
        s.history.append({"role": "user", "content": text})
        if not u.low:
            s.state.barge_repeats = 0       # anlaşılır söz: gürültü yinelemesi sayacı sıfırlanır
        for name in STEPS:
            if (turn := await getattr(self, name)(s, u)) is not None:
                log.debug("[%s] cevap veren adım: %s", s.call_id, name)
                return turn
        raise AssertionError("son adım (_step_answer) her zaman cevap verir")

    def _empty_turn(self, s: DialogSession, u: _Utterance, only_fillers: bool) -> DialogTurn:
        """STT boş ya da yalnız dolgu sesi ("eee", "şey") verdi: açık soru açık kalır."""
        u.reopen(s)
        if only_fillers:
            # arayan düşünüyor; deneme sayılmaz, açık soru ya da "anlayamadım" (LLM yok)
            log.info("[%s] yalnız dolgu sesi", s.call_id)
            if u.lenient and not self.pending_question(s) and (again := self._repeat_cut(s)) is not None:
                return again                 # canlı 05.11.2026: söze giren gürültü "şey" (0,31) → "anlayamadım"
            reply = self._reply(s, self.pending_question(s) or self.cfg.texts.not_understood)
            reply.fast = True
            return reply
        if u.lenient and (again := self._repeat_cut(s)) is not None:
            return again
        return self._failure(s, count=not u.lenient)

    def _reply(self, s: DialogSession, text: str) -> DialogTurn:
        actions, s.state.actions = s.state.actions, []
        if not text:
            t = next((a for a in actions if isinstance(a, TransferAction)), None)
            text = self._transfer_text(t.contact_id) if t else self.cfg.texts.not_understood
        s.history.append({"role": "assistant", "content": text})
        return DialogTurn(text, list[Action](actions))
