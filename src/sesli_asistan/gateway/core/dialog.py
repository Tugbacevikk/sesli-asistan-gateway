"""Diyalog yöneticisi: LLM araç döngüsü + koda bağlı diyalog kuralları (tasarim.md 5.1, 7.5, 9).

LLM'e bırakılanlar: arayanın ne istediğini anlamak, aracı doğru parametreyle çağırmak, kısa ve
doğal bir cevap yazmak.
Koda bağlı olanlar (LLM unutsa ya da kandırılsa bile çalışır):
  - Aktarma yalnızca TransferPolicy'den geçerse yapılır (tools.py, policy.py).
  - **Net eşleşmede aktarma kodla yapılır (ADR-012):** InContacts `single_high` dönerse aktarma
    eylemi TransferPolicy'den geçirilip doğrudan üretilir ve sabit cümle söylenir; ikinci bir LLM turu
    beklenmez. (Ölçüm, 26.10.2026: qwen3.5:4b `single_high` sonrası çoğu zaman "aktarıyorum" deyip
    TransferCall'ı çağırmadı; ayrıca her tur bir LLM çağrısı demek.)
  - **İki deneme kuralı:** ilerleme olmayan her arayan turu sayılır (arama sonuçsuz ya da hiç arama/eylem
    yok); 2. başarısızlıkta operatör teklif edilir, 3.'de doğrudan operatöre aktarılır. Arayan hiçbir
    zaman döngüye hapsolmaz; LLM aramayı atlasa bile sayaç ilerler.
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

from sesli_asistan.libs.text.normalizer import turkish_lower

from .models import Candidate, DialogTurn, Match, OperatorAction
from .ports import LlmError, LlmPort, ToolCall
from .search import has_role_word, query_variants, search_key, similarity, strip_suffix
from .tools import CallToolState, ToolRegistry

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """Sen {kurum} santralinin sesli asistanısın. Arayanla yalnızca Türkçe konuşursun.
Görevin arayanı doğru kişiye veya birime yönlendirmektir.

Kurallar:
- Cevapların telefonda sesli okunacak: en fazla iki kısa cümle. Liste, emoji, sembol kullanma.
- Kişi veya birim sorulduğunda önce InContacts aracını kullan. Rehberde olmayan bilgiyi asla uydurma.
- Arayan yalnızca birim söylerse adı Department alanına, kişi söylerse Name alanına yaz; unvan söylerse
  ("satış müdürü", "muhasebe uzmanı") unvanın tamamını Name alanına yaz; dahili numara söylerse Extension
  alanına rakamlarla yaz.
- Araç sonucundaki "match" alanına göre davran:
  single_low → doğru kişi olup olmadığını sor; arayan onaylarsa TransferCall ile aktar,
  multiple → seçenekleri adıyla say ve hangisi olduğunu sor; arayan seçince TransferCall ile aktar,
  none → nazikçe adı ya da birimi tekrar söylemesini iste.
- "Aktarıyorum" ya da "bağlıyorum" demeden önce mutlaka TransferCall aracını çağır.
- ConnectOperator aracını yalnızca arayan açıkça operatör isterse ya da operatör teklifini kabul ederse kullan.
- Dahili numarayı arayan sormadıkça söyleme.
- Yönlendirme dışındaki konularda yardımcı olamayacağını kibarca belirt.
- Yalnızca Türkçe kelimeler kullan.
- "[Santral]" ile başlayan mesajlar santralden gelen bilgidir, arayan söylememiştir."""


@dataclass(frozen=True)
class DialogTexts:
    """Koddan söylenen sabit cümleler (ayardan değiştirilebilir; bkz. tasarim.md 8)."""

    greeting: str = "{kurum_e} hoş geldiniz. Ben sesli asistanım, kiminle görüşmek istersiniz?"
    not_understood: str = "Sizi anlayamadım. Görüşmek istediğiniz kişinin adını ya da birimi tekrar söyler misiniz?"
    offer_operator: str = ("Sizi yine anlayamadım. İsterseniz sizi operatöre bağlayabilirim, "
                           "ya da adı tekrar söyleyebilirsiniz.")
    no_input: str = "Sizi duyamıyorum. Görüşmek istediğiniz kişinin adını ya da birimi söyleyebilirsiniz."
    to_operator: str = "Sizi operatöre bağlıyorum, lütfen hatta kalın."
    error: str = "Şu an size yardımcı olamıyorum. Sizi operatöre bağlıyorum, lütfen hatta kalın."
    transferring: str = "Sizi {ad_e} aktarıyorum."
    transferring_department: str = "Sizi {birim} birimine aktarıyorum."
    closed: str = ("Şu an mesai saatlerimiz dışındayız. Lütfen mesai saatlerimiz içinde tekrar arayın. "
                   "İyi günler dileriz.")
    holiday: str = ("Bugün tatil nedeniyle hizmet veremiyoruz. Lütfen mesai saatlerimiz içinde tekrar arayın. "
                    "İyi günler dileriz.")
    no_operator: str = ("Şu an operatörümüz hizmet vermiyor. Lütfen mesai saatlerimiz içinde tekrar arayın. "
                        "İyi günler dileriz.")
    offer_operator_suffix: str = "İsterseniz sizi operatöre de bağlayabilirim."
    offer_operator_note: str = ("Arayanın istediği iki kez bulunamadı: nazikçe operatöre bağlanmayı "
                                "teklif et ya da tekrar söylemesini iste.")


@dataclass(frozen=True)
class DialogConfig:
    organization: str = ""    # boşsa kurum adı söylenmez (ORGANIZATION_NAME)
    max_failed_attempts: int = 3
    max_tool_rounds: int = 3
    max_history: int = 16
    llm_timeout_s: float = 5.0
    auto_transfer_single_high: bool = True   # ADR-012
    fast_path: bool = True                   # ADR-016: net tam ad / birim hattında LLM beklenmez
    fast_path_min_score: int = 95
    clarify_by_code: bool = True             # ADR-017: birden çok adayda "X mı, Y mi?" sorusu ve seçimi LLM'siz
    texts: DialogTexts = field(default_factory=DialogTexts)


# Bu sözcüklerden biri geçerse hızlı yol kapalı: arayan düzeltiyor, reddediyor ya da başka bir şey istiyor olabilir
_FAST_BLOCK = {"degil", "hayir", "yok", "istemiyorum", "olmasin", "baska", "yerine", "operator", "iptal", "vazgectim",
               "ama", "veya", "ya", "kim", "hangi", "nerede", "numara", "numarasi", "dahili"}


class DialogSession:
    """Tek bir çağrının diyalog durumu (yalnızca bellekte, tasarim.md 8.1)."""

    def __init__(self, agent: DialogAgent, call_id: str):
        self.agent = agent
        self.call_id = call_id
        self.state = CallToolState()
        self.history: list[dict[str, Any]] = []

    def greeting(self) -> DialogTurn:
        text = greeting_text(self.agent.cfg)
        self.history.append({"role": "assistant", "content": text})
        return DialogTurn(text)

    async def handle_user(self, text: str) -> DialogTurn:
        return await self.agent.handle_user(self, text)

    async def handle_transfer_result(self, status: str) -> DialogTurn:
        return await self.agent.handle_transfer_result(self, status)


_BACK, _FRONT = set("aıou"), set("eiöü")


def dative(name: str) -> str:
    """Özel ada Türkçe yönelme hâli eki (ünlü uyumu): Kartal → Kartal'a, Bursa → Bursa'ya, Deniz → Deniz'e."""
    word = (name or "").strip()
    vowels = [ch for ch in word.lower() if ch in _BACK | _FRONT]
    if not vowels:
        return word
    suffix = "a" if vowels[-1] in _BACK else "e"
    if word[-1].lower() in _BACK | _FRONT:
        suffix = "y" + suffix
    return f"{word}'{suffix}"


GREETING_NO_ORG = "Hoş geldiniz. Ben sesli asistanım, kiminle görüşmek istersiniz?"


def greeting_text(cfg: DialogConfig) -> str:
    """Karşılama. Kurum adı verilmediyse ve metin kurum adı istiyorsa kurumsuz karşılama kullanılır."""
    org = cfg.organization.strip()
    if not org and "{kurum" in cfg.texts.greeting:
        return GREETING_NO_ORG
    return cfg.texts.greeting.format(kurum=org, kurum_e=dative(org))


def fixed_prompts(cfg: DialogConfig) -> dict[str, str]:
    """Koddan söylenen sabit cümleler: açılışta seslendirilip saklanır (speech.PromptLibrary)."""
    t = cfg.texts
    return {"greeting": greeting_text(cfg), "not_understood": t.not_understood,
            "offer_operator": t.offer_operator, "to_operator": t.to_operator, "error": t.error,
            "no_input": t.no_input, "closed": t.closed, "holiday": t.holiday, "no_operator": t.no_operator}


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
        org = self.cfg.organization.strip()
        self.system_prompt = SYSTEM_PROMPT.format(kurum=f"{org} şirketinin" if org else "bir şirketin")

    def new_session(self, call_id: str) -> DialogSession:
        return DialogSession(self, call_id)

    def fixed_prompts(self) -> dict[str, str]:
        return fixed_prompts(self.cfg)

    # --- giriş noktaları ---

    async def handle_user(self, s: DialogSession, text: str) -> DialogTurn:
        text = _BRACKETS.sub(" ", text or "").strip()  # arayan "[Santral]" taklidi yapamasın
        if not text:
            return self._failure(s)
        s.history.append({"role": "user", "content": text})
        choice, s.state.choice = s.state.choice, ()
        if choice and self.cfg.clarify_by_code and (turn := self._resolve_choice(s, text, choice)) is not None:
            return turn
        if self.cfg.fast_path and (turn := self._fast_path(s, text)) is not None:
            return turn
        if self.cfg.fast_path and self.cfg.clarify_by_code and (turn := self._fast_clarify(s, text)) is not None:
            return turn
        return await self._run(s)

    # --- belirsiz aramada soru ve seçim (ADR-017) ---

    def _clarify(self, s: DialogSession, candidates: tuple[Candidate, ...]) -> DialogTurn | None:
        """2–3 kişilik adaydan "Muhasebeden Ayşe Yılmaz mı, satıştan Ayşe Kaya mı?" sorusu (LLM'siz). Adaylar
        sorudan ayırt edilemiyorsa (aynı ad, aynı birim) None: LLM sorsun."""
        if not 2 <= len(candidates) <= 3 or any(c.contact.kind != "person" for c in candidates):
            return None
        q = which_question([c.contact for c in candidates])
        if q is None:
            return None
        s.state.choice = candidates
        return self._reply(s, q)

    def _fast_clarify(self, s: DialogSession, text: str) -> DialogTurn | None:
        """Sözde yalnızca birden çok kişiye uyan bir ad var ("ayşe hanım"): LLM beklemeden sorulur. Sözde bir birim,
        unvan ya da olumsuzluk varsa LLM'e bırakılır (birimle birlikte arama gerekir)."""
        words = search_key(text).split()
        if not words or _FAST_BLOCK & set(words) or any(strip_suffix(w) in _FAST_BLOCK for w in words):
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
                if res.match != Match.MULTIPLE or not all(
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
                log.info("[%s] hızlı soru: %r → %s", s.call_id, text, [c.contact.extension for c in s.state.choice])
                return turn
        return None

    def _mentions_department(self, words: list[str]) -> bool:
        keys = {k for c in self.tools.index.contacts() for k in
                (search_key(c.department), *map(search_key, c.department_aliases)) if k}
        text = " ".join(strip_suffix(w) for w in words)
        return any(f" {strip_suffix(k)} " in f" {text} " or any(w.startswith(k) for w in words if len(k) >= 4)
                   for k in keys)

    def _resolve_choice(self, s: DialogSession, text: str, choice: tuple[Candidate, ...]) -> DialogTurn | None:
        """Sorulan adaylardan birini cevaptan seçer: birim ("muhasebedeki"), soyad ("Yılmaz"), tam ad ya da sıra
        ("ilki", "ikincisi"). Tek aday seçilmezse ya da arayan reddederse None: LLM devam eder."""
        words = search_key(text).split()
        if not words or _FAST_BLOCK & set(words) or any(strip_suffix(w) in _FAST_BLOCK for w in words):
            return None
        picked: set[int] = set()
        for i, cand in enumerate(choice):
            c = cand.contact
            dept_keys = [k for k in (search_key(c.department), *map(search_key, c.department_aliases)) if k]
            last = search_key(c.last_name)
            for w in words:
                variants = query_variants(w)
                if last and any(similarity(v, last) >= 90 for v in variants):
                    picked.add(i)
                if any(len(k) >= 4 and (w.startswith(k) or similarity(strip_suffix(w), k) >= 90)
                       for k in dept_keys if " " not in k):
                    picked.add(i)
            if any(" " in k and k in " ".join(words) for k in dept_keys):
                picked.add(i)
        for word, idx in _ORDINALS.items():
            if word in words and -len(choice) <= idx < len(choice):
                picked.add(idx % len(choice))
        if len(picked) != 1:
            return None
        cand = choice[picked.pop()]
        s.state.last_results = choice
        action = self.tools.transfer(cand.contact.extension, s.state)
        if action is None:
            return None
        s.state.failed_attempts = 0
        log.info("[%s] seçim: %r → %s", s.call_id, text, cand.contact.extension)
        turn = self._reply(s, self._transfer_text(action.contact_id))
        turn.fast = True
        return turn

    # --- hızlı yol (ADR-016) ---

    def _mentions_person(self, words: list[str]) -> bool:
        """Sözde bir kişinin adı ya da soyadı geçiyor mu (birim hattına kendiliğinden gitmemek için)."""
        known = self.tools.index.person_words
        for w in words:
            for v in query_variants(w):
                if v in known or (len(v) >= 4 and any(similarity(v, k) >= 92 for k in known)):
                    return True
        return False

    def _fast_path(self, s: DialogSession, text: str) -> DialogTurn | None:
        """Arayanın sözünde bir kişinin **tam adı** (ad + soyad) ya da bir **birimin genel hattı** tek ve kesin
        geçiyorsa LLM'e gitmeden aktarır: "Ayşe Yılmaz ile görüşmek istiyorum", "teknik destek lütfen". LLM çağrısı tur
        gecikmesinin %60'ı (ölçüm 27.10.2026). Şüphede (olumsuzluk, birden çok aday, yalnız ad, unvan, düşük skor)
        hiçbir şey yapmaz; LLM normal akışla devam eder. Aktarma yine TransferPolicy'den geçer."""
        words = search_key(text).split()
        if not words or _FAST_BLOCK & set(words) or any(strip_suffix(w) in _FAST_BLOCK for w in words):
            return None
        index = self.tools.index
        found: dict[str, tuple[Any, str]] = {}
        if (c := index.alias_match(text)) is not None and c.kind == "person":
            found[c.id] = (c, "alias")                # "Ayşe Abla": takma adın tam hâli tek kişiye ait
        for n in (3, 2, 1):
            for i in range(len(words) - n + 1):
                window = " ".join(words[i:i + n])
                for kind in ("name", "department"):
                    if kind == "name" and n < 2:
                        continue                       # yalnız ad ("ayşe") ya da soyad: kime olduğu belirsiz
                    res = index.search(**{kind: window})
                    if res.match != Match.SINGLE_HIGH or res.candidates[0].score < self.cfg.fast_path_min_score:
                        continue
                    c = res.candidates[0].contact
                    if kind == "name":
                        full = search_key(c.full_name)
                        # unvan ya da takma adla değil, gerçekten ad + soyadla eşleşmeli
                        if c.kind != "person" or len(full.split()) < 2 or max(
                                similarity(q, full) for q in query_variants(window)) < self.cfg.fast_path_min_score:
                            continue
                    if kind == "department":
                        if c.kind != "department" or has_role_word(text) or self._mentions_person(words):
                            continue                   # "muhasebeden ayşe hanım": birim değil, oradaki bir kişi
                    found.setdefault(c.id, (c, kind))
        if len(found) != 1:
            return None                                # hiç ya da birden çok aday: LLM karar versin
        contact, kind = next(iter(found.values()))
        args = ({"Department": contact.department} if kind == "department"
                else {"Name": contact.full_name})
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
        log.info("[%s] hızlı yol: %r → %s (%s)", s.call_id, text, contact.extension, contact.full_name)
        turn = self._reply(s, self._transfer_text(action.contact_id))
        turn.fast = True
        return turn

    async def handle_transfer_result(self, s: DialogSession, status: str) -> DialogTurn:
        pending = s.state.pending_transfer
        s.state.pending_transfer = None
        if status == "answered":
            return DialogTurn("", ended=True)
        reasons = {"busy": "meşgul", "no_answer": "cevap vermiyor", "failed": "aktarma başarısız oldu"}
        who = next((c.contact.full_name for c in s.state.last_results
                    if pending and c.contact.extension == pending.extension), "aranan kişi")
        s.history.append({"role": "user", "content": f"[Santral] {who} {reasons.get(status, 'ulaşılamadı')}. "
                          "Şimdi araç çağırma; arayana bildir ve başka birine mi yoksa operatöre mi "
                          "aktarılmak istediğini sor."})
        return await self._run(s, caller_turn=False)

    # --- LLM döngüsü ---

    async def _run(self, s: DialogSession, caller_turn: bool = True) -> DialogTurn:
        """caller_turn=False: santral olayından sonra (deneme sayacına girmez)."""
        found = failed = stop = False
        for _ in range(self.cfg.max_tool_rounds):
            try:
                # Santral olayından sonra LLM yalnızca konuşur: araç verilmez (ölçüm 26.10.2026, D24/D25:
                # "araç çağırma" istemine rağmen operatöre bağladı / aynı kişiye tekrar aktardı)
                tools = self.tools.schemas if caller_turn else []
                msg = await asyncio.wait_for(self.llm.chat(self._messages(s), tools),
                                             timeout=self.cfg.llm_timeout_s)
            except (TimeoutError, LlmError) as e:
                log.error("[%s] LLM hatası (%s): operatöre aktarılıyor", s.call_id, e or type(e).__name__)
                return self._operator(s, self.cfg.texts.error, reason="llm_hatasi")

            if not msg.tool_calls:
                text = clean_reply(msg.content)
                if caller_turn and not found and not failed and not s.state.actions:
                    # LLM aramadan cevap verdi ve ilerleme yok: deneme sayılır
                    if (turn := self._count_failure(s)) is not None:
                        return turn
                    if s.state.failed_attempts == self.cfg.max_failed_attempts - 1 and "operatör" not in text:
                        text = f"{text} {self.cfg.texts.offer_operator_suffix}".strip()
                return self._reply(s, text)

            s.history.append(_assistant_tool_message(msg.content, msg.tool_calls))
            for call in msg.tool_calls:
                out = self.tools.execute(call, s.state)
                content = dict(out.content)
                if out.failed_search and caller_turn and not failed:
                    failed = True
                    s.state.failed_attempts += 1
                    if s.state.failed_attempts >= self.cfg.max_failed_attempts:
                        s.history.append(_tool_message(call, content))
                        return self._operator(s, self.cfg.texts.to_operator, reason="bulunamadi")
                    if s.state.failed_attempts == self.cfg.max_failed_attempts - 1:
                        content["note"] = self.cfg.texts.offer_operator_note
                elif call.name == "InContacts" and not out.failed_search:
                    found = True
                    s.state.failed_attempts = 0
                if (self.cfg.clarify_by_code and caller_turn and call.name == "InContacts"
                        and content.get("match") == "multiple" and len(msg.tool_calls) == 1):
                    s.history.append(_tool_message(call, content))
                    if (turn := self._clarify(s, s.state.last_results)) is not None:
                        return turn               # ikinci LLM turu beklenmez (ADR-017)
                    s.history.pop()
                if (self.cfg.auto_transfer_single_high and call.name == "InContacts"
                        and content.get("match") == "single_high"):
                    action = self.tools.transfer(s.state.last_results[0].contact.extension, s.state)
                    if action is not None:
                        content["transferred"] = True
                        s.history.append(_tool_message(call, content))
                        return self._reply(s, self._transfer_text(action.contact_id))
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
            text = self._transfer_text(t.contact_id) if t else self.cfg.texts.not_understood
        s.history.append({"role": "assistant", "content": text})
        return DialogTurn(text, actions)

    def _transfer_text(self, contact_id: str) -> str:
        c = self.tools.index.get(contact_id)
        if c is None:
            return self.cfg.texts.to_operator
        if c.kind == "department":
            return self.cfg.texts.transferring_department.format(birim=c.department)
        return self.cfg.texts.transferring.format(ad=c.full_name, ad_e=dative(c.full_name))

    def _count_failure(self, s: DialogSession) -> DialogTurn | None:
        """Deneme sayacını artırır; sınıra ulaşıldıysa operatör turunu döner."""
        s.state.failed_attempts += 1
        if s.state.failed_attempts >= self.cfg.max_failed_attempts:
            return self._operator(s, self.cfg.texts.to_operator, reason="ilerleme_yok")
        return None

    def _failure(self, s: DialogSession) -> DialogTurn:
        """STT boş/anlamsız metin verdi: LLM'e gitmeden sabit cümle, deneme sayacı artar."""
        if (turn := self._count_failure(s)) is not None:
            return turn
        n, limit = s.state.failed_attempts, self.cfg.max_failed_attempts
        text = self.cfg.texts.offer_operator if n == limit - 1 else self.cfg.texts.not_understood
        s.history.append({"role": "assistant", "content": text})
        return DialogTurn(text, fallback=True)

    def _operator(self, s: DialogSession, text: str, reason: str) -> DialogTurn:
        s.state.actions = []
        s.history.append({"role": "assistant", "content": text})
        return DialogTurn(text, [OperatorAction(reason=reason)], fallback=True)


_ORDINALS = {"birinci": 0, "birincisi": 0, "ilk": 0, "ilki": 0, "ilkine": 0, "ikinci": 1, "ikincisi": 1,
             "ikinciye": 1, "ucuncu": 2, "ucuncusu": 2, "son": -1, "sonuncu": -1, "sonuncusu": -1, "sonuncuya": -1}
_VOWELS = "aıoueiöü"


def _last_vowel(word: str) -> str:
    return next((ch for ch in reversed(turkish_lower(word)) if ch in _VOWELS), "e")


def question_particle(word: str) -> str:
    """Soru eki (ünlü uyumu): Yılmaz → mı, Demir → mi, Koç → mu, Öztürk → mü."""
    return {"a": "mı", "ı": "mı", "o": "mu", "u": "mu", "e": "mi", "i": "mi", "ö": "mü", "ü": "mü"}[_last_vowel(word)]


def ablative(word: str) -> str:
    """Ayrılma hâli: Muhasebe → Muhasebeden, Satış → Satıştan, Bilgi İşlem → Bilgi İşlemden,
    İnsan Kaynakları → İnsan Kaynaklarından."""
    w = word.strip()
    last = turkish_lower(w)[-1:]
    # tamlama ("İnsan Kaynakları", "Müşteri Hizmetleri"): iyelik ekinden sonra -n- kaynaştırma → Kaynaklarından
    buffer = "n" if " " in w and last in "ıiuü" else ""
    hard = not buffer and last in set("fstkçşhp")
    return w + buffer + ("t" if hard else "d") + ("a" if _last_vowel(w) in "aıou" else "e") + "n"


def which_question(contacts: list[Any]) -> str | None:
    """"Muhasebeden Ayşe Yılmaz mı, Satıştan Ayşe Kaya mı?" — birimler farklıysa birimle, adlar farklıysa adla.
    Hiçbiri ayırt etmiyorsa None."""
    names = [c.full_name for c in contacts]
    depts = [c.department for c in contacts]
    use_dept = all(depts) and len({search_key(d) for d in depts}) == len(depts)
    if not use_dept and len({search_key(n) for n in names}) < len(names):
        return None
    parts = [f"{ablative(d) + ' ' if use_dept else ''}{n} {question_particle(n)}" for n, d in zip(names, depts,
                                                                                                strict=True)]
    text = ", ".join(parts[:-1]) + (", yoksa " if len(parts) > 2 else ", ") + parts[-1] + "?"
    return text[0].upper() + text[1:]


def _assistant_tool_message(content: str, calls: list[ToolCall]) -> dict[str, Any]:
    return {"role": "assistant", "content": content or "",
            "tool_calls": [{"id": c.id, "type": "function",
                            "function": {"name": c.name,
                                         "arguments": c.raw_arguments or json.dumps(c.arguments, ensure_ascii=False)}}
                           for c in calls]}


def _tool_message(call: ToolCall, content: dict[str, Any]) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": call.id, "name": call.name,
            "content": json.dumps(content, ensure_ascii=False)}
