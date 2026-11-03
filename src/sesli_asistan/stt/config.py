"""STT servisi ayarları (tasarim.md 13). Ortam değişkeni ya da `/etc/sesli-asistan/stt.env`."""
from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SttSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="STT_", env_file=(".env", "/etc/sesli-asistan/stt.env"),
                                      extra="ignore")

    host: str = "127.0.0.1"                 # yalnızca yerel (tasarim.md 10.2)
    port: int = 50051
    max_streams: int = Field(64, ge=1)      # eşzamanlı çağrı akışı üst sınırı

    # Motor: sherpa (varsayılan, mihu akışlı transducer, CPU; stt/sherpa_engine.py) | whisper (GPU, stt/engine.py).
    # Okuma ölçümü (03.11.2026): mihu hatasız, en düşük WER, RTF 0,07 (CPU) — ADR-021, docs/olcumler.md
    engine: Literal["whisper", "sherpa"] = "sherpa"
    sherpa_dir: str = "models/turkish-stt-66m-v2"
    sherpa_threads: int = Field(2, ge=1)

    # Gateway'in rehber ad ipuçları motora verilsin mi. Kapalı: okuma ölçümünde (03.11.2026, tools/stt/gorev_olc.py,
    # telefon hattı, 23 cümle) ipucu bozuk sesi rehber adına çevirdi ("operatöre bağlanmak istiyorum" → "ayşe
    # yılmaz"); güvenle teyit sorulsa da doğru iş azaldı, yanlış teyit arttı (medium: 9 → 5 doğru, 1 → 6 yanlış teyit).
    hotwords: bool = False

    # Whisper (stt/engine.py)
    model_dir: str = "models/faster-whisper-medium"
    device: Literal["auto", "cuda", "cpu"] = "auto"
    compute_type: str = "auto"
    beam: int = Field(2, ge=1, le=10)
    num_workers: int = Field(1, ge=1)       # aynı anda çalışan Whisper çözümü
    cpu_threads: int = Field(4, ge=1)
    timeout_s: float = Field(3.0, gt=0)     # bir cümlenin çözümü bundan uzun sürerse FINAL atlanır (log)

    # VAD / cümle sonu (stt/vad.py)
    vad: Literal["silero", "energy"] = "silero"
    vad_model: str = "models/silero_vad.onnx"
    # Silero eşiği. 0,35: santral denemelerinde (02.11.2026, tools/stt/vad_incele.py) hoparlör + yankı gidericiyle gelen
    # konuşmanın olasılığı 0,4–0,8'de kalıyordu (0,5 kısa cevapları kaçırdı); 0,35 yalnız gerçek konuşma ekledi,
    # 0,2 karşılama anonsunun yankısını ve santral sesini de konuşma saydı.
    vad_threshold: float = Field(0.35, gt=0, lt=1)
    endpoint_silence_ms: int = Field(700, ge=100)
    speech_started_ms: int = Field(300, ge=0)   # bu kadar sesli konuşmadan sonra SPEECH_STARTED (barge-in)
    min_voiced_s: float = Field(0.15, gt=0, le=1)   # bundan kısa sesli süre cümle sayılmaz (öksürük, tık); vad.py
    metrics_port: int = Field(9101, ge=0)   # Prometheus (yalnız 127.0.0.1); 0: kapalı
    debug_dir: str = ""    # teşhis: cümle sesleri + sonuçları + çağrının bütün sesi (ses kaydı! üretimde boş bırakın)
    max_utterance_s: float = Field(12.0, gt=1)
