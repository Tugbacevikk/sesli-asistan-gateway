"""TTS servisi ayarları. Ortam değişkeni (TTS_SESLEN_ öneki) ya da `/etc/sesli-asistan/tts.env`."""
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TtsSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TTS_SESLEN_", env_file=(".env", "/etc/sesli-asistan/tts.env"),
                                      extra="ignore")

    host: str = "127.0.0.1"          # yalnızca yerel (tasarim.md 10.2)
    port: int = Field(5600, gt=0, lt=65536)
    voice: str = "ayse"              # olgun kadın sesi, güven veren; diğerleri: zeynep, fatma, emre, hasan, koca
    tone: str = "notr"               # santral için sabit, sakin ton ("otomatik": cümleye göre duygu, ek paket ister)
    model_dir: str = ""              # boş: ilk açılışta indirilir (~330 MB); internetsiz kurulumda model klasörü
    threads: int = Field(0, ge=0)    # ONNX iş parçacığı (0: otomatik)
    workers: int = Field(2, ge=1)    # aynı anda seslendirme
    rate: int = 24000                # Seslen çıkışı
