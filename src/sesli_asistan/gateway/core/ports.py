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


# --- Ses çıkışı (Aşama 3) ---

@dataclass(frozen=True)
class Audio:
    """Mono int16 PCM."""

    pcm: Any            # np.ndarray[int16]
    rate: int

    @property
    def seconds(self) -> float:
        return len(self.pcm) / self.rate if self.rate else 0.0


class TtsError(Exception):
    """TTS servisine ulaşılamadı, zaman aşımı ya da geçersiz ses."""


class TtsPort(Protocol):
    async def synthesize(self, text: str) -> Audio: ...


class AudioOutPort(Protocol):
    """Sesi arayana çalar (Aşama 5'te RtpSender). İptal edilebilir olmalı (barge-in)."""

    async def play(self, pcm8k: Any) -> int: ...


# --- Konuşmadan metne (Aşama 4) ---

@dataclass(frozen=True)
class SttEvent:
    type: str                 # "speech_started" | "partial" | "final" | "not_recognized"
    text: str = ""
    confidence: float = 0.0
    start_ms: int = 0
    end_ms: int = 0
    reason: str = ""          # not_recognized: "timeout" | "error" | "empty"


class SttError(Exception):
    """STT servisine ulaşılamadı ya da akış koptu."""


class SttStream(Protocol):
    """Bir çağrının STT akışı: ses kareleri gönderilir, olaylar okunur."""

    async def send(self, payload: bytes) -> None: ...

    def events(self) -> Any: ...      # AsyncIterator[SttEvent]

    async def close(self) -> None: ...


class SttPort(Protocol):
    async def open(self, call_id: str, codec: str, hotwords: list[str]) -> SttStream: ...


# --- Santral (Aşama 5) ---

class MediaPort(Protocol):
    """Bir çağrının ses yolu (RTP): gelen 20 ms G.711 kareleri + arayana ses çalma (tasarim.md 6.1)."""

    def frames(self) -> Any: ...          # AsyncIterator[bytes]; akış bitince durur

    async def play(self, pcm8k: Any) -> int: ...

    def idle_s(self) -> float: ...        # son RTP paketinden bu yana geçen süre (hiç gelmediyse açılıştan)

    def stats(self) -> dict[str, int]: ...

    def close(self) -> None: ...


class PbxError(Exception):
    """Santral eylem isteği (yeniden denemelerden sonra da) başarısız."""


class PbxPort(Protocol):
    """Gateway → santral eylemleri (tasarim.md 6.2.2). Sonuç asenkron olarak /events ile gelir."""

    async def send_action(self, call_id: str, action: dict[str, Any]) -> None: ...
