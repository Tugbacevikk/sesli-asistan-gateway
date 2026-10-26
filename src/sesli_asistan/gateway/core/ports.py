"""Çekirdeğin dış dünyaya açılan arayüzleri (hexagonal: portlar). Adaptörler `gateway/adapters/` altında."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str = ""        # ayrıştırılamadıysa hatayı LLM'e dönmek için


@dataclass(frozen=True)
class LlmMessage:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)


class LlmError(Exception):
    """LLM'e ulaşılamadı, zaman aşımı ya da geçersiz yanıt."""


class LlmPort(Protocol):
    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LlmMessage: ...


@dataclass(frozen=True)
class DirectoryPage:
    server_time: str
    contacts: list[dict[str, Any]]
    full: bool = False   # True: tam liste (eldeki kopyanın yerine geçer); False: yalnızca değişenler


class DirectorySource(Protocol):
    """Rehber kaynağı: Sentra Rehber API ya da aynı formatta JSON dosyası (tasarim.md 6.6)."""

    async def fetch(self, updated_since: str | None = None) -> DirectoryPage: ...
