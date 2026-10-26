"""Gateway ayarları (tasarim.md 13). Ortam değişkeninden ya da `/etc/sesli-asistan/gateway.env`'den okunur.

Bu dosya Aşama 2'nin (diyalog) ayarlarını içerir; santral, RTP ve STT ayarları ilgili aşamalarda eklenir.
Geçersiz ayarda servis açıklayıcı hatayla başlamaz (pydantic doğrulaması).
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class GatewaySettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "/etc/sesli-asistan/gateway.env"), extra="ignore")

    organization_name: str = "Şirket"

    # LLM (ADR-005)
    llm_backend: Literal["ollama", "openai"] = "ollama"
    llm_base_url: str = "http://127.0.0.1:11434"         # openai için ör. http://127.0.0.1:8080/v1
    llm_model: str = "qwen3.5:4b"                        # ADR-005; alternatif: qwen3.5:9b
    llm_temperature: float = Field(0.2, ge=0, le=2)
    llm_timeout_s: float = Field(5.0, gt=0)

    # Rehber (ADR-008)
    directory_source: Literal["json", "sentra"] = "json"
    directory_path: str = "data/rehber_ornek.json"
    directory_url: str = ""
    directory_refresh_s: float = Field(300, gt=0)

    # Diyalog kuralları (tasarim.md 7.5)
    match_min_score: int = Field(70, ge=0, le=100)
    match_high_score: int = Field(90, ge=0, le=100)
    match_margin: int = Field(5, ge=0, le=50)
    max_failed_attempts: int = Field(3, ge=1)
    max_tool_rounds: int = Field(3, ge=1)
    max_history: int = Field(16, ge=4)

    # TTS (tasarim.md 5.4, 6.4)
    tts_url: str = "http://127.0.0.1:5500"
    tts_path: str = "/synthesize"
    tts_format: Literal["json", "plain", "query"] = "json"
    tts_voice: str | None = None              # boş: servisin varsayılan sesi
    tts_length_scale: float | None = None     # konuşma hızı (1 = normal; büyüdükçe yavaş)
    tts_timeout_s: float = Field(3.0, gt=0)
    tts_cache_items: int = Field(256, ge=0)
    tts_cache_dir: str | None = None           # üretimde: /var/cache/sesli-asistan/tts (boş: yalnızca bellek)
