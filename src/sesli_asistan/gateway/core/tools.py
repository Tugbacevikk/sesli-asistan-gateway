"""LLM araçları: tanımlar (JSON Schema) ve yürütücüler (tasarim.md 6.5).

Araç tanımları tek yerde durur. Araçlar Gateway içinde çalışır ve çağrı başına durum (`CallToolState`) üzerinde
işlem yapar. `LLM_TOOLS=mcp` (ADR-022): LLM'in rehber araması (`InContacts`) rehber MCP sunucusunda yapılır (aynı
arama: `in_contacts`); sonuç Gateway'in kendi rehberindeki kayıtlara çevrilir, aktarma yine buradaki
TransferPolicy'den geçer. MCP ulaşılamazsa arama burada yapılır. `MCP_STEP` (ADR-023): LLM'den önce MCP'ye konu
sorulur (`find_topic`); dönen kimlikler yine Gateway'in kendi rehberinde aranır.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from .models import Candidate, Match, OperatorAction, SearchResult, TransferAction
from .people import resolve_person
from .policy import TransferPolicy
from .ports import RemoteToolsPort, ToolCall
from .search import DirectoryIndex, has_role_word
from .topics import Topic, TopicTable

log = logging.getLogger(__name__)

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "InContacts",
            "description": "Şirket rehberinde kişi veya birim arar. Arayan biriyle ya da bir birimle "
                           "görüşmek istediğinde her zaman önce bunu kullan.",
            "parameters": {
                "type": "object",
                "properties": {
                    "Name": {"type": "string",
                             "description": "Kişinin adı, soyadı ya da unvanı (ör. 'satış müdürü'); bilinmiyorsa boş"},
                    "Department": {"type": "string", "description": "Birim/departman adı; bilinmiyorsa boş"},
                    "Extension": {"type": "string",
                                  "description": "Arayan dahili numarayı söylediyse rakamlarla (ör. 214); yoksa boş"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "TransferCall",
            "description": "Arayanı InContacts ile bulunan bir dahiliye aktarır. Yalnızca InContacts "
                           "sonucunda geçen bir dahili numarayla kullan.",
            "parameters": {
                "type": "object",
                "properties": {"Extension": {"type": "string", "description": "Dahili numara"}},
                "required": ["Extension"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ConnectOperator",
            "description": "Arayanı operatöre bağlar. Yalnızca arayan açıkça operatör isterse ya da "
                           "operatör teklifini kabul ederse kullan.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


@dataclass
class CallToolState:
    """Bir çağrının araç durumu (tasarim.md 8.1)."""

    last_results: tuple[Candidate, ...] = ()
    choice: tuple[Candidate, ...] = ()      # arayana "X mı, Y mi?" diye soruldu: cevabı bu adaylardan biri
    choice_question: str = ""               # o soru "X mı, Y mi?" değilse ("Hangi birimdeki müfettiş?")
    confirm: Candidate | None = None        # arayana "X'e mi bağlayayım?" diye soruldu (STT güveni düşüktü)
    confirm_suggested: bool = False         # o teyit bir öneriydi (konudan çıkarılan birim): "hayır" → "Peki, kiminle?"
    confirm_operator: bool = False          # arayana "operatöre mi bağlayayım?" diye soruldu (hedef anlaşılmadı)
    truncated: bool = False                 # son aramada gösterilenden çok aday vardı
    pending_transfer: TransferAction | None = None
    unreachable: set[str] = field(default_factory=set)   # bu çağrıda ulaşılamayan dahililer (meşgul, cevapsız, hata)
    failed_attempts: int = 0
    barge_repeats: int = 0                  # söze girme anlaşılmadı, yarıda kalan cümle üst üste yinelendi
    actions: list[TransferAction | OperatorAction] = field(default_factory=list)
    misses: list[str] = field(default_factory=list)   # bulunamayan aramalar (rehbere takma ad eklemek için)


@dataclass(frozen=True)
class ToolOutcome:
    content: dict[str, Any]
    failed_search: bool = False      # InContacts hiçbir şey bulamadı (deneme sayacı)
    stop: bool = False               # bu turda başka LLM turu gerekmiyor (ör. operatör)


def in_contacts(index: DirectoryIndex, args: dict[str, Any]) -> tuple[SearchResult, dict[str, Any], str]:
    """InContacts araması: (sonuç, LLM'e giden içerik, sorgu özeti). Gateway ve rehber MCP sunucusu aynı işlevi
    kullanır: iki yol birebir aynı sonucu verir (ADR-022)."""
    name, dept, ext = _str(args.get("Name")), _str(args.get("Department")), _str(args.get("Extension"))
    if dept and not name and has_role_word(dept):
        # "satış müdürü" birim değil, unvanıyla bir kişi: unvan araması (ölçüm 26.10.2026, D07)
        name, dept = dept, ""
    res = index.search(name, dept, ext)
    if res.match == Match.NONE and not ext and bool(name) != bool(dept):
        # LLM alanı karıştırmış olabilir ("İK"yi Name'e yazmak gibi): diğer alanla bir kez daha dene
        res = index.search(dept, name)
    content: dict[str, Any] = {"match": res.match.value, "results": [c.to_tool() for c in res.candidates]}
    if res.truncated:
        # yalnız ilk `limit` aday gösterildi: LLM bunlar arasında seçtirmesin, ayırt edici bilgi istesin
        content["more"] = res.total - len(res.candidates)
        if res.candidates and all(c.contact.kind == "role" for c in res.candidates):
            content["note"] = ("Bu aramaya uyan birden çok birim/görev hattı var (kayıtlarda kişi soyadı yoktur, "
                               "asla soyadı sorma); arayandan hangi birim, şube veya görevliyle görüşmek istediğini iste.")
        else:
            content["note"] = "Bu ada uyan daha çok kişi var; arayandan soyadı ya da birimi iste."
    return res, content, " / ".join(x for x in (name, dept, ext) if x)


class ToolRegistry:
    def __init__(self, index: DirectoryIndex, policy: TransferPolicy | None = None,
                 remote: RemoteToolsPort | None = None, remote_timeout_s: float = 1.0, remote_search: bool = True,
                 topics: Sequence[Topic] = ()):
        """`remote_search`: LLM'in InContacts araması MCP'de (LLM_TOOLS=mcp); False ise `remote` yalnız konu adımı.
        `topics`: MCP sunucusununkiyle aynı konu listesi; MCP'ye ulaşılamazsa konu adımı ve birden çok hedef
        denetimi bununla yerelde çalışır."""
        self.index = index
        self.topics = list(topics)
        self._topic_table: tuple[int, TopicTable] | None = None
        self.policy = policy or TransferPolicy()
        self.rejections = 0          # TransferPolicy'nin reddettiği aktarmalar (metrik: güvenlik incelemesi)
        self.remote, self.remote_timeout_s = remote, remote_timeout_s
        self.remote_calls = 0        # MCP'ye giden arama (metrik)
        self.remote_failures = 0     # MCP ulaşılamadı / zaman aşımı: yerel aramaya düşüldü
        self.remote_search = remote_search
        self.topic_calls = self.topic_hits = self.topic_failures = 0      # konu adımı (metrik)
        self.person_calls = self.person_hits = self.person_failures = 0   # kişi çözme adımı (metrik)

    @property
    def schemas(self) -> list[dict[str, Any]]:
        return TOOLS

    def execute(self, call: ToolCall, state: CallToolState) -> ToolOutcome:
        if call.raw_arguments and not call.arguments:
            return ToolOutcome({"ok": False, "error": "INVALID_ARGUMENTS", "detail": "Parametreler JSON değil"})
        handler = {"InContacts": self._in_contacts, "TransferCall": self._transfer,
                   "ConnectOperator": self._operator}.get(call.name)
        if handler is None:
            return ToolOutcome({"ok": False, "error": "UNKNOWN_TOOL", "detail": call.name})
        out = handler(call.arguments, state)
        log.info("araç %s(%s) → %s", call.name, json.dumps(call.arguments, ensure_ascii=False),
                 json.dumps(out.content, ensure_ascii=False))
        return out

    async def execute_async(self, call: ToolCall, state: CallToolState) -> ToolOutcome:
        """LLM'in araç çağrısı. `LLM_TOOLS=mcp` ise rehber araması MCP sunucusunda (zaman sınırlı); ulaşılamazsa ya
        da başka araçsa `execute` (yerel)."""
        if (self.remote is not None and self.remote_search and call.name == "InContacts"
                and not (call.raw_arguments and not call.arguments)):
            self.remote_calls += 1
            try:
                data = await asyncio.wait_for(self.remote.in_contacts(call.arguments), self.remote_timeout_s)
                return self._remote_in_contacts(call, data, state)
            except Exception as e:                    # zaman aşımı, bağlantı, bozuk cevap: arayan fark etmez
                self.remote_failures += 1
                log.warning("MCP rehber araması olmadı (%s): yerel arama", e or type(e).__name__)
        return self.execute(call, state)

    async def resolve_person(self, text: str) -> tuple[tuple[Candidate, ...], bool, tuple[Candidate, ...]]:
        """Akışın 2. adımı (ADR-025): MCP'de kişi çözme (unvan, birim + ad, ad + hitap) → (adaylar, kesin mi, ses
        benzerliğiyle aday — kurallar bulamadıysa, yalnız teyitle kullanılır). MCP
        ulaşılamazsa aynı işlev burada çalışır (`core.people.resolve_person`): adım kaybolmaz. Kimlikler Gateway'in
        kendi rehberinde aranır (bulunmayan atılır)."""
        data: dict[str, Any] | None = None
        if self.remote is not None:
            self.person_calls += 1
            try:
                data = await asyncio.wait_for(self.remote.resolve_person(text), self.remote_timeout_s)
            except Exception as e:
                self.person_failures += 1
                log.warning("MCP kişi çözme olmadı (%s): yerel", e or type(e).__name__)
        remote = data is not None
        if data is None:
            data = resolve_person(self.index, text)
        cands = tuple(Candidate(c, 100) for a in data.get("adaylar", []) if isinstance(a, dict)
                      if (c := self.index.get(str(a.get("id", "")))) is not None)
        if cands and remote:
            self.person_hits += 1           # metrik: MCP'nin cevabı (konu adımıyla aynı: bulundu / yok / hata)
        sound = tuple(Candidate(c, int(a.get("puan", 0))) for a in data.get("ses") or [] if isinstance(a, dict)
                      if (c := self.index.get(str(a.get("id", "")))) is not None)[:1]
        return cands, bool(data.get("kesin")) and len(cands) == 1, sound

    async def find_topic(self, text: str) -> tuple[Candidate, ...]:
        """Akışın 2. adımı: MCP'ye sözdeki konu sorulur. Dönen kimlikler Gateway'in kendi rehberinde aranır
        (bulunmayan atılır: MCP sunucusu kayıt uyduramaz). MCP yoksa, zaman aşımı ya da hata → () (söz LLM'e gider)."""
        if self.remote is None:
            return self.local_topic(text)
        self.topic_calls += 1
        try:
            data = await asyncio.wait_for(self.remote.find_topic(text), self.remote_timeout_s)
        except Exception as e:
            self.topic_failures += 1
            log.warning("MCP konu araması olmadı (%s): yerel konu listesi", e or type(e).__name__)
            return self.local_topic(text)
        cands = tuple(Candidate(c, 100) for a in data.get("adaylar", []) if isinstance(a, dict)
                      if (c := self.index.get(str(a.get("id", "")))) is not None)
        if cands:
            self.topic_hits += 1
            a0 = data["adaylar"][0]
            log.info("MCP konu: %r → %s (%s)", a0.get("eslesen"), a0.get("birim"), [c.contact.extension for c in cands])
        return cands

    def topic_table(self) -> TopicTable | None:
        """Yerel konu tablosu (rehber sürümüne göre önbellekte); konu listesi yoksa None."""
        if not self.topics:
            return None
        ix = self.index
        if self._topic_table is None or self._topic_table[0] != ix.version:
            self._topic_table = (ix.version, TopicTable(ix.contacts(), self.topics))
        return self._topic_table[1]

    def local_topic(self, text: str) -> tuple[Candidate, ...]:
        """Sözdeki konu → birim (yerel konu listesiyle, MCP'ye gitmeden); konu listesi yoksa ()."""
        if (table := self.topic_table()) is None:
            return ()
        return tuple(Candidate(c, 100) for a in table.lookup(text) if (c := self.index.get(a["id"])) is not None)

    def _remote_in_contacts(self, call: ToolCall, data: dict[str, Any], state: CallToolState) -> ToolOutcome:
        """MCP sonucu → çağrı durumu. Adaylar Gateway'in KENDİ rehberinden alınır (kimlikle): MCP sunucusu bir kayıt
        uyduramaz; aktarma politikası yalnız bu adaylara izin verir."""
        content = dict(data["content"])
        match = Match(content["match"])
        cands = tuple(Candidate(c, int(a.get("puan", 0))) for a in data.get("adaylar", [])
                      if (c := self.index.get(str(a.get("id", "")))) is not None)
        state.last_results = cands
        state.truncated = "more" in content
        if match == Match.NONE:
            args = call.arguments
            state.misses.append(" / ".join(_str(args.get(k)) for k in ("Name", "Department", "Extension")
                                           if _str(args.get(k))))
        log.info("araç %s(%s) → (MCP) %s", call.name, json.dumps(call.arguments, ensure_ascii=False),
                 json.dumps(content, ensure_ascii=False))
        return ToolOutcome(content, failed_search=match == Match.NONE)

    def _in_contacts(self, args: dict[str, Any], state: CallToolState) -> ToolOutcome:
        res, content, query = in_contacts(self.index, args)
        state.last_results = res.candidates
        if res.match == Match.NONE:
            state.misses.append(query)
        state.truncated = res.truncated
        return ToolOutcome(content, failed_search=res.match == Match.NONE)

    def transfer(self, extension: str, state: CallToolState) -> TransferAction | None:
        """Aktarma eylemi üretir — yalnızca TransferPolicy izin verirse. LLM aracı da, kodun kendi
        kararı (single_high, ADR-012) da bu tek kapıdan geçer."""
        decision = self.policy.check(extension, state.last_results, pending=state.pending_transfer is not None)
        if not decision.allowed:
            self.rejections += 1
            log.warning("GÜVENLİK: aktarma reddedildi (%s): dahili=%r", decision.reason, extension)
            return None
        assert decision.candidate is not None          # izin verildiyse aday var (policy.check)
        c = decision.candidate.contact
        action = TransferAction(extension=c.extension, contact_id=c.id)
        state.pending_transfer = action
        state.actions.append(action)
        return action

    def _transfer(self, args: dict[str, Any], state: CallToolState) -> ToolOutcome:
        action = self.transfer(_str(args.get("Extension")), state)
        if action is None:
            return ToolOutcome({"ok": False, "error": "TRANSFER_NOT_ALLOWED",
                                "detail": "Yalnızca InContacts sonucundaki bir dahiliye aktarabilirsin."})
        c = self.index.get(action.contact_id)
        return ToolOutcome({"ok": True, "name": c.full_name if c else "", "department": c.department if c else ""})

    def _operator(self, args: dict[str, Any], state: CallToolState) -> ToolOutcome:
        state.actions.append(OperatorAction(reason="arayan_istedi"))
        return ToolOutcome({"ok": True}, stop=True)


def _str(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ""
