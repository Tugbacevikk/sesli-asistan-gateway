"""Gateway ayarları (tasarim.md 13). Ortam değişkeninden ya da `/etc/sesli-asistan/gateway.env`'den okunur.

Geçersiz ayarda servis açıklayıcı hatayla başlamaz (pydantic doğrulaması). Gizli değerler (`API_KEY`,
`PBX_API_KEY`) `/etc/sesli-asistan/secrets.env`'de tutulur (izin 600) ve loglara yazılmaz.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .core.dialog import DialogTexts

_TEXT_FIELDS = ("greeting", "not_understood", "offer_operator", "no_input", "to_operator", "error", "transferring",
                "transferring_department")


class GatewaySettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "/etc/sesli-asistan/gateway.env",
                                                "/etc/sesli-asistan/secrets.env"), extra="ignore")

    organization_name: str = ""                          # karşılamada söylenen kurum adı; boşsa söylenmez

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

    # Santral REST + RTP (tasarim.md 6.1, 6.2, 13)
    gateway_api_host: str = "0.0.0.0"
    gateway_api_port: int = Field(8000, gt=0, lt=65536)
    api_key: SecretStr = SecretStr("")        # santralin Gateway'e gönderdiği Bearer anahtar; boş: kimlik doğrulama yok
    rtp_host: str = "0.0.0.0"
    rtp_port_min: int = Field(10000, gt=0, lt=65536)
    rtp_port_max: int = Field(10999, gt=0, lt=65536)
    pbx_allowed_ips: str = ""                 # virgülle; boş: her kaynaktan RTP ve REST kabul (yalnızca geliştirme)
    pbx_base_url: str = "http://127.0.0.1:8100"
    pbx_api_key: SecretStr = SecretStr("")
    pbx_timeout_s: float = Field(3.0, gt=0)
    max_concurrent_calls: int = Field(30, ge=1)
    rtp_idle_timeout_s: float = Field(20, gt=0)
    max_call_duration_s: float = Field(600, gt=0)
    transfer_result_timeout_s: float = Field(30, gt=0)
    barge_in: bool = True
    no_input_s: float = Field(8.0, gt=0)      # dinlemede sessizlik → "Sizi duyamıyorum"
    no_input_max: int = Field(2, ge=1)        # kaçıncı sessizlikte operatöre

    # Koddan söylenen cümleler (tasarim.md 13). Boş: varsayılan metin (core/dialog.py DialogTexts).
    # Yer tutucular: {kurum} {kurum_e} (karşılama), {ad} {ad_e} (kişiye aktarma), {birim} (birime aktarma).
    greeting_text: str = ""
    not_understood_text: str = ""
    offer_operator_text: str = ""
    no_input_text: str = ""
    to_operator_text: str = ""
    error_text: str = ""
    transferring_text: str = ""
    transferring_department_text: str = ""

    # STT servisi (Gateway tarafı)
    stt_grpc_addr: str = "127.0.0.1:50051"

    def dialog_texts(self) -> DialogTexts:
        """Ayardan verilen metinler varsayılanların üzerine yazılır."""
        given = {f: getattr(self, f"{f}_text") for f in _TEXT_FIELDS if getattr(self, f"{f}_text").strip()}
        return replace(DialogTexts(), **given)

    @model_validator(mode="after")
    def _check(self) -> GatewaySettings:
        if self.rtp_port_min > self.rtp_port_max:
            raise ValueError(f"RTP_PORT_MIN ({self.rtp_port_min}) > RTP_PORT_MAX ({self.rtp_port_max})")
        if self.rtp_port_max - self.rtp_port_min + 1 < self.max_concurrent_calls:
            raise ValueError("RTP port aralığı MAX_CONCURRENT_CALLS'tan küçük")
        t = self.dialog_texts()
        for name, text, needs in (("GREETING_TEXT", t.greeting, ()), ("TRANSFERRING_TEXT", t.transferring, ()),
                                  ("TRANSFERRING_DEPARTMENT_TEXT", t.transferring_department, ("{birim}",))):
            try:
                text.format(kurum="K", kurum_e="K'ye", ad="A", ad_e="A'ya", birim="B")
            except (KeyError, IndexError, ValueError) as e:
                raise ValueError(f"{name}: geçersiz yer tutucu {e} (kullanılabilir: {{kurum}} {{kurum_e}} "
                                 "{ad} {ad_e} {birim})") from None
            if any(n not in text for n in needs):
                raise ValueError(f"{name} şunu içermeli: {', '.join(needs)}")
        if "{ad" not in t.transferring:
            raise ValueError("TRANSFERRING_TEXT {ad} ya da {ad_e} içermeli")
        return self

    @property
    def allowed_ips(self) -> set[str] | None:
        ips = {x.strip() for x in self.pbx_allowed_ips.split(",") if x.strip()}
        return ips or None
