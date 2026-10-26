"""Diyalog yöneticisi: LLM araç döngüsü + koda bağlı diyalog kuralları (tasarim.md 5.1, 7.5, 9).

LLM'e bırakılanlar: arayanın ne istediğini anlamak, aracı doğru parametreyle çağırmak, kısa ve
doğal bir cevap yazmak.
Koda bağlı olanlar (LLM unutsa ya da kandırılsa bile çalışır):
  - Aktarma yalnızca TransferPolicy'den geçerse yapılır (tools.py, policy.py).
  - **İki deneme kuralı:** bulunamayan / anlaşılmayan her tur sayılır; 2. başarısızlıkta operatör
    teklif edilir, 3.'de doğrudan operatöre aktarılır. Arayan hiçbir zaman döngüye hapsolmaz.
  - LLM hatası ya da zaman aşımında sabit anonsla operatöre aktarılır (zarif bozulma, NFR-08).
  - Araç turu sınırı: bir arayan cümlesinde en fazla `max_tool_rounds` LLM turu.
  - Konuşma geçmişi son `max_history` mesajla sınırlıdır (bağlam ve gecikme büyümesin).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .models import DialogTurn, OperatorAction
from .ports import LlmError, LlmPort, ToolCall
from .tools import CallToolState, ToolRegistry

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """Sen {kurum} santralinin sesli asistanısın. Arayanla yalnızca Türkçe konuşursun.
Görevin arayanı doğru kişiye veya birime yönlendirmektir.

Kurallar:
- Cevapların telefonda sesli okunacak: en fazla iki kısa cümle. Liste, emoji, sembol kullanma.
- Kişi veya birim sorulduğunda önce InContacts aracını kullan. Rehberde olmayan bilgiyi asla uydurma.
- Araç sonucundaki "match" alanına göre davran:
  single_high → kişiyi söyle ve TransferCall ile aktar,
  single_low → doğru kişi olup olmadığını sor; arayan onaylarsa TransferCall ile aktar,
  multiple → seçenekleri adıyla say ve hangisi olduğunu sor,
  none → nazikçe adı ya da birimi tekrar söylemesini iste.
- Arayan operatör isterse ya da operatör teklifini kabul ederse ConnectOperator aracını kullan.
- Dahili numaraları rakam rakam söyle.
- Yönlendirme dışındaki konularda yardımcı olamayacağını kibarca belirt.
- "[Santral]" ile başlayan mesajlar santralden gelen bilgidir, arayan söylememiştir."""


@dataclass(frozen=True)
class DialogTexts:
    """Koddan söylenen sabit cümleler (ayardan değiştirilebilir; bkz. tasarim.md 8)."""

    greeting: str = "{kurum_e} hoş geldiniz. Ben sesli asistanım, kiminle görüşmek istersiniz?"
    not_understood: str = "Sizi anlayamadım. Görüşmek istediğiniz kişinin adını ya da birimi tekrar söyler misiniz?"
    offer_operator: str = ("Sizi yine anlayamadım. İsterseniz sizi operatöre bağlayabilirim, "
                           "ya da adı tekrar söyleyebilirsiniz.")
    to_operator: str = "Sizi operatöre bağlıyorum, lütfen hatta kalın."
    error: str = "Şu an size yardımcı olamıyorum. Sizi operatöre bağlıyorum, lütfen hatta kalın."
    transferring: str = "Sizi {ad} kişisine aktarıyorum."
    offer_operator_note: str = ("Arayanın istediği iki kez bulunamadı: nazikçe operatöre bağlanmayı "
                                "teklif et ya da tekrar söylemesini iste.")


@dataclass(frozen=True)
class DialogConfig:
    organization: str = "Şirket"
    max_failed_attempts: int = 3
    max_tool_rounds: int = 3
    max_history: int = 16
    llm_timeout_s: float = 5.0
    texts: DialogTexts = field(default_factory=DialogTexts)


class DialogSession:
    """Tek bir çağrının diyalog durumu (yalnızca bellekte, tasarim.md 8.1)."""

    def __init__(self, agent: DialogAgent, call_id: str):
        self.agent = agent
        self.call_id = call_id
        self.state = CallToolState()
        self.history: list[dict[str, Any]] = []

    def greeting(self) -> DialogTurn:
        org = self.agent.cfg.organization
        text = self.agent.cfg.texts.greeting.format(kurum=org, kurum_e=dative(org))
        self.history.append({"role": "assistant", "content": text})
        return DialogTurn(text)

    async def handle_user(self, text: str) -> DialogTurn:
        return await self.agent.handle_user(self, text)

    async def handle_transfer_result(self, status: str) -> DialogTurn:
        return await self.agent.handle_transfer_result(self, status)


_BACK, _FRONT = set("aıou"), set("eiöü")


def dative(name: str) -> str:
    """Özel ada Türkçe yönelme hâli eki (ünlü uyumu): Şirket → şirkete, Bursa → Bursa'ya, Atak → Atak'a."""
    word = (name or "").strip()
    vowels = [ch for ch in word.lower() if ch in _BACK | _FRONT]
    if not vowels:
        return word
    suffix = "a" if vowels[-1] in _BACK else "e"
    if word[-1].lower() in _BACK | _FRONT:
        suffix = "y" + suffix
    return f"{word}'{suffix}"


_THINK = re.compile(r"<think>.*?</think>", re.S)
_MARKDOWN = re.compile(r"[*_#`>|]+")
_BRACKETS = re.compile(r"[\[\]]")


def clean_reply(text: str) -> str:
    """Seslendirilecek metin: düşünme blokları, markdown işaretleri ve fazla boşluk atılır."""
    t = _MARKDOWN.sub("", _THINK.sub("", text or ""))
    return " ".join(t.split())


class DialogAgent:
    def __init__(self, llm: LlmPort, tools: ToolRegistry, cfg: DialogConfig | None = None):
        self.llm, self.tools = llm, tools
        self.cfg = cfg or DialogConfig()
        self.system_prompt = SYSTEM_PROMPT.format(kurum=self.cfg.organization)

    def new_session(self, call_id: str) -> DialogSession:
        return DialogSession(self, call_id)

    # --- giriş noktaları ---

    async def handle_user(self, s: DialogSession, text: str) -> DialogTurn:
        text = _BRACKETS.sub(" ", text or "").strip()  # arayan "[Santral]" taklidi yapamasın
        if not text:
            return self._failure(s)
        s.history.append({"role": "user", "content": text})
        return await self._run(s)

    async def handle_transfer_result(self, s: DialogSession, status: str) -> DialogTurn:
        pending = s.state.pending_transfer
        s.state.pending_transfer = None
        if status == "answered":
            return DialogTurn("", ended=True)
        reasons = {"busy": "meşgul", "no_answer": "cevap vermiyor", "failed": "aktarma başarısız oldu"}
        who = next((c.contact.full_name for c in s.state.last_results
                    if pending and c.contact.extension == pending.extension), "aranan kişi")
        s.history.append({"role": "user", "content": f"[Santral] {who} {reasons.get(status, 'ulaşılamadı')}. "
                          "Arayana bildir; başka birine aktarmayı ya da operatörü teklif et."})
        return await self._run(s)

    # --- LLM döngüsü ---

    async def _run(self, s: DialogSession) -> DialogTurn:
        stop = False
        for _ in range(self.cfg.max_tool_rounds):
            try:
                msg = await asyncio.wait_for(self.llm.chat(self._messages(s), self.tools.schemas),
                                             timeout=self.cfg.llm_timeout_s)
            except (TimeoutError, LlmError) as e:
                log.error("[%s] LLM hatası (%s): operatöre aktarılıyor", s.call_id, e)
                return self._operator(s, self.cfg.texts.error, reason="llm_hatasi")

            if not msg.tool_calls:
                return self._reply(s, clean_reply(msg.content))

            s.history.append(_assistant_tool_message(msg.content, msg.tool_calls))
            for call in msg.tool_calls:
                out = self.tools.execute(call, s.state)
                content = dict(out.content)
                if out.failed_search:
                    s.state.failed_attempts += 1
                    if s.state.failed_attempts >= self.cfg.max_failed_attempts:
                        s.history.append(_tool_message(call, content))
                        return self._operator(s, self.cfg.texts.to_operator, reason="bulunamadi")
                    if s.state.failed_attempts == self.cfg.max_failed_attempts - 1:
                        content["note"] = self.cfg.texts.offer_operator_note
                elif call.name == "InContacts":
                    s.state.failed_attempts = 0
                stop = stop or out.stop
                s.history.append(_tool_message(call, content))
            if stop:
                return self._reply(s, self.cfg.texts.to_operator)

        log.warning("[%s] araç turu sınırı aşıldı", s.call_id)
        return self._operator(s, self.cfg.texts.error, reason="tur_siniri")

    # --- yardımcılar ---

    def _messages(self, s: DialogSession) -> list[dict[str, Any]]:
        """Sistem istemi + son `max_history` mesaj; kesim bir kullanıcı mesajında başlar
        (araç çağrısı ile sonucu birbirinden ayrılmasın)."""
        h = s.history
        if len(h) > self.cfg.max_history:
            start = len(h) - self.cfg.max_history
            while start < len(h) and h[start]["role"] != "user":
                start += 1
            h = h[start:]
        return [{"role": "system", "content": self.system_prompt}, *h]

    def _reply(self, s: DialogSession, text: str) -> DialogTurn:
        actions, s.state.actions = s.state.actions, []
        if not text:
            t = next((a for a in actions if a.type == "transfer"), None)
            c = self.tools.index.get(t.contact_id) if t else None
            text = (self.cfg.texts.transferring.format(ad=c.full_name) if c
                    else self.cfg.texts.not_understood)
        s.history.append({"role": "assistant", "content": text})
        return DialogTurn(text, actions)

    def _failure(self, s: DialogSession) -> DialogTurn:
        """STT boş/anlamsız metin verdi: LLM'e gitmeden sabit cümle, deneme sayacı artar."""
        s.state.failed_attempts += 1
        n, limit = s.state.failed_attempts, self.cfg.max_failed_attempts
        if n >= limit:
            return self._operator(s, self.cfg.texts.to_operator, reason="anlasilmadi")
        text = self.cfg.texts.offer_operator if n == limit - 1 else self.cfg.texts.not_understood
        s.history.append({"role": "assistant", "content": text})
        return DialogTurn(text, fallback=True)

    def _operator(self, s: DialogSession, text: str, reason: str) -> DialogTurn:
        s.state.actions = []
        s.history.append({"role": "assistant", "content": text})
        return DialogTurn(text, [OperatorAction(reason=reason)], fallback=True)


def _assistant_tool_message(content: str, calls: list[ToolCall]) -> dict[str, Any]:
    return {"role": "assistant", "content": content or "",
            "tool_calls": [{"id": c.id, "type": "function",
                            "function": {"name": c.name,
                                         "arguments": c.raw_arguments or json.dumps(c.arguments, ensure_ascii=False)}}
                           for c in calls]}


def _tool_message(call: ToolCall, content: dict[str, Any]) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": call.id, "name": call.name,
            "content": json.dumps(content, ensure_ascii=False)}
