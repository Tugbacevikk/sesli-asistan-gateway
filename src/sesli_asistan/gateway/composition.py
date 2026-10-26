"""Bileşenleri ayarlardan kurar (bağımlılıkların tek birleştiği yer)."""
from __future__ import annotations

from .adapters.directory.json_dir import JsonDirectory
from .adapters.llm.ollama import OllamaChat
from .adapters.llm.openai_compat import OpenAiCompatChat
from .config import GatewaySettings
from .core.dialog import DialogAgent, DialogConfig
from .core.directory import DirectoryCache
from .core.ports import LlmPort
from .core.search import DirectoryIndex
from .core.tools import ToolRegistry


def build_llm(s: GatewaySettings) -> LlmPort:
    if s.llm_backend == "ollama":
        return OllamaChat(s.llm_base_url, s.llm_model, s.llm_temperature, s.llm_timeout_s)
    return OpenAiCompatChat(s.llm_base_url, s.llm_model, s.llm_temperature, s.llm_timeout_s)


def build_directory(s: GatewaySettings) -> DirectoryCache:
    if s.directory_source != "json":
        raise NotImplementedError("Sentra Rehber API adaptörü Aşama 9'da (API yazılınca)")
    index = DirectoryIndex(min_score=s.match_min_score, high_score=s.match_high_score, margin=s.match_margin)
    return DirectoryCache(JsonDirectory(s.directory_path), index, s.directory_refresh_s)


def build_agent(s: GatewaySettings, directory: DirectoryCache, llm: LlmPort | None = None) -> DialogAgent:
    cfg = DialogConfig(organization=s.organization_name, max_failed_attempts=s.max_failed_attempts,
                       max_tool_rounds=s.max_tool_rounds, max_history=s.max_history,
                       llm_timeout_s=s.llm_timeout_s)
    return DialogAgent(llm or build_llm(s), ToolRegistry(directory.index), cfg)
