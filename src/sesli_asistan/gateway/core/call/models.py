"""Çağrının veri tipleri: durum (tasarim.md 5.1.1), ayarlar, tur kaydı ve çağrı özeti (izleme / geçmiş)."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ..thresholds import (
    BACKGROUND_SPEECH_MARGIN_DB,
    BARGE_IN_LEVEL_MARGIN_DB,
    MIN_NOISE_MS,
    NOISE_BELOW,
    NOISE_MAX_LETTERS,
)


class CallState(StrEnum):
    GREETING = "greeting"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    TRANSFERRING = "transferring"   # aktarma eylemi santralde, sonuç bekleniyor
    HANDED_OFF = "handed_off"       # aktarıldı / operatöre verildi: santralin DELETE'i beklenir
    ENDED = "ended"


TRANSFER_STATUSES = ("answered", "busy", "no_answer", "failed")


@dataclass(frozen=True)
class CallConfig:
    rtp_idle_timeout_s: float = 20.0
    max_call_duration_s: float = 600.0
    transfer_result_timeout_s: float = 30.0
    barge_in: bool = True
    barge_in_level_margin_db: float = BARGE_IN_LEVEL_MARGIN_DB   # ses seviyesi kapısı (core/level.py); 0: kapalı
    background_margin_db: float = BACKGROUND_SPEECH_MARGIN_DB   # arka plan konuşması kapısı; 0: kapalı
    min_noise_ms: int = MIN_NOISE_MS   # bundan kısa "anlaşılamadı" (boş) sesler gürültü sayılır, cevaplanmaz
    # Gürültü kırıntısı: güveni bunun altında ve en çok `noise_max_letters` harf ("b"): cevaplanmaz, deneme sayılmaz
    # (canlı 04.11.2026: ortam sesi "b" (0,05) → "anlayamadım"). 0: kapalı
    noise_below: float = NOISE_BELOW
    noise_max_letters: int = NOISE_MAX_LETTERS
    dtmf_operator_digit: str = "0"     # FR-15: tuşlayınca operatör
    no_input_s: float = 8.0            # dinlemede bu kadar sessizlik → "Sizi duyamıyorum"
    no_input_max: int = 2              # kaçıncı sessizlikte operatöre
    tick_s: float = 1.0
    thinking_sound: bool = True        # düşünürken klavye sesi (sessizlik "hat koptu mu?" dedirtmesin)
    thinking_sound_delay_s: float = 0.4   # cevap bundan hızlı gelirse ses hiç çalınmaz
    after_hours_mode: str = "announce"    # mesai dışı: announce (anons + kapat) | assistant (operatör yerine anons)


@dataclass
class TurnRecord:
    """Bir turun ölçümleri (log ve uçtan uca ölçüm aracı için)."""

    heard: str
    reply: str
    actions: list[str]
    dialog_s: float                    # LLM + araçlar
    final_to_audio_s: float | None     # STT metni geldi → arayana ilk ses (turn_latency, tasarim.md 12.2)
    barged: bool = False
    llm_calls: int = 0                 # 0: tur kodla çözüldü (hızlı yol, soru, seçim, sabit cümle)


@dataclass
class CallStats:
    turns: list[TurnRecord] = field(default_factory=list)
    barge_ins: int = 0
    barge_ins_ignored: int = 0          # ses seviyesi kapısının saymadığı söze girmeler (arka plan sesi)
    background_ignored: int = 0         # arayanın seviyesinin çok altında kaldığı için cevaplanmayan sözler
    tts_failures: int = 0
    no_inputs: int = 0
    outcome: str = ""                  # transferred | operator | closed | hangup | error
    reason: str = ""
    stt_dropped: int = 0
    rtp: dict[str, int] = field(default_factory=dict)
    channel: str = "santral"           # santral | web (arayüzden deneme)
    started: str = ""                  # yerel saat, ISO
    duration_s: float = 0.0
    misses: list[str] = field(default_factory=list)   # rehberde bulunamayan aramalar


@dataclass
class Event:
    """Oturumun olay kuyruğundaki bir olay (STT, santral, zamanlayıcı, konuşma bitti); yalnız döngüde işlenir."""

    kind: str
    data: dict[str, Any] = field(default_factory=dict)
    at: float = 0.0
