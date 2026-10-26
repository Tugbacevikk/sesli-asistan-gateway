"""LLM araçları: tanımlar (JSON Schema) ve yürütücüler (tasarim.md 6.5).

Araç tanımları tek yerde durur; ileride aynen bir MCP sunucusu olarak dışarı açılabilir.
Araçlar Gateway içinde çalışır ve çağrı başına durum (`CallToolState`) üzerinde işlem yapar.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from .models import Candidate, Match, OperatorAction, TransferAction
from .policy import TransferPolicy
from .ports import ToolCall
from .search import DirectoryIndex

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
                    "Name": {"type": "string", "description": "Kişinin adı, soyadı ya da unvanı; bilinmiyorsa boş"},
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
    pending_transfer: TransferAction | None = None
    failed_attempts: int = 0
    actions: list[TransferAction | OperatorAction] = field(default_factory=list)


@dataclass(frozen=True)
class ToolOutcome:
    content: dict[str, Any]
    failed_search: bool = False      # InContacts hiçbir şey bulamadı (deneme sayacı)
    stop: bool = False               # bu turda başka LLM turu gerekmiyor (ör. operatör)


class ToolRegistry:
    def __init__(self, index: DirectoryIndex, policy: TransferPolicy | None = None):
        self.index = index
        self.policy = policy or TransferPolicy()

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

    def _in_contacts(self, args: dict[str, Any], state: CallToolState) -> ToolOutcome:
        name, dept, ext = _str(args.get("Name")), _str(args.get("Department")), _str(args.get("Extension"))
        res = self.index.search(name, dept, ext)
        if res.match == Match.NONE and not ext and bool(name) != bool(dept):
            # LLM alanı karıştırmış olabilir ("İK"yi Name'e yazmak gibi): diğer alanla bir kez daha dene
            res = self.index.search(dept, name)
        state.last_results = res.candidates
        content = {"match": res.match.value, "results": [c.to_tool() for c in res.candidates]}
        return ToolOutcome(content, failed_search=res.match == Match.NONE)

    def transfer(self, extension: str, state: CallToolState) -> TransferAction | None:
        """Aktarma eylemi üretir — yalnızca TransferPolicy izin verirse. LLM aracı da, kodun kendi
        kararı (single_high, ADR-012) da bu tek kapıdan geçer."""
        decision = self.policy.check(extension, state.last_results, pending=state.pending_transfer is not None)
        if not decision.allowed:
            log.warning("GÜVENLİK: aktarma reddedildi (%s): dahili=%r", decision.reason, extension)
            return None
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
