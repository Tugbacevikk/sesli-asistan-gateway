"""Kadın sesli Türkçe TTS servisi: Seslen (seslen-tts, ONNX, CPU) → Piper ile aynı HTTP sözleşmesi (ADR-015).

    POST /synthesize   {"text": "...", "voice": "ayse"?, "length_scale": 1.0?}  →  audio/wav (24 kHz, 16 bit, mono)
    GET  /             sağlık: {"status": "ok", "voice": ..., "voices": [...]}

Gateway'de değişen bir şey yoktur: `TTS_URL` bu servise çevrilir (Piper yerine). Gateway sesi 8 kHz'e kendisi indirir.

Neden ayrı süreç: model ağırlıkları CC BY-SA 4.0 (Piper gibi, kod MIT ama sesler ayrı lisans); Gateway'in bağımlılığı
olmaz, gerekirse başka makineye taşınır.

Kullanım koşulu (model sahibinin): üretilen sesin yapay zekâ sesi olduğu dinleyiciye belirtilir — karşılama "Ben sesli
asistanım" der. Gerçek kişi taklidi yasaktır.

    python -m sesli_asistan.tts                 # TTS_SESLEN_VOICE=ayse, port 5600
"""
from __future__ import annotations

import asyncio
import io
import logging
import threading
import time
import wave
from typing import Any, Protocol

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from .config import TtsSettings

log = logging.getLogger(__name__)


class Engine(Protocol):
    sesler: dict

    def tum(self, metin: str, **ayar: Any) -> np.ndarray: ...


class SynthesizeRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    voice: str | None = None
    length_scale: float | None = Field(None, gt=0.3, lt=3.0)   # Piper ile aynı anlam: büyüdükçe yavaş


def to_wav(x: np.ndarray, rate: int) -> bytes:
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def load_engine(s: TtsSettings) -> Engine:
    from seslen_tts import Seslen  # ağır içe aktarma: yalnız gerçek serviste

    return Seslen(klasor=s.model_dir or None, is_parcacigi=s.threads)


def create_app(s: TtsSettings, engine: Engine | None = None) -> FastAPI:
    state: dict[str, Any] = {"engine": engine}
    lock = threading.Semaphore(s.workers)     # ONNX oturumu başına aynı anda en çok `workers` seslendirme

    async def lifespan(app):
        if state["engine"] is None:
            t0 = time.perf_counter()
            state["engine"] = await asyncio.to_thread(load_engine, s)
            await asyncio.to_thread(_speak, "Merhaba.", s.voice, None)          # ısınma
            log.info("Seslen hazır: ses=%s, %.1f sn", s.voice, time.perf_counter() - t0)
        if s.voice not in state["engine"].sesler:
            raise RuntimeError(f"bilinmeyen ses: {s.voice}; sesler: {', '.join(state['engine'].sesler)}")
        yield

    app = FastAPI(title="Seslen TTS", lifespan=lifespan, docs_url=None, redoc_url=None)

    def _speak(text: str, voice: str, length_scale: float | None) -> np.ndarray:
        ayar: dict[str, Any] = {"ses": voice, "ton": s.tone}
        if length_scale:
            ayar["hiz"] = 1.0 / length_scale
        with lock:
            x = state["engine"].tum(text, **ayar)
        peak = float(np.abs(x).max()) if len(x) else 0.0
        return x / peak * 0.9 if peak > 0 else x

    @app.get("/")
    async def health() -> JSONResponse:
        eng = state["engine"]
        return JSONResponse({"status": "ok" if eng else "starting", "voice": s.voice,
                             "voices": list(eng.sesler) if eng else []})

    @app.post("/synthesize")
    async def synthesize(req: SynthesizeRequest) -> Response:
        voice = req.voice or s.voice
        if voice not in state["engine"].sesler:
            raise HTTPException(400, f"bilinmeyen ses: {voice}")
        t0 = time.perf_counter()
        try:
            x = await asyncio.to_thread(_speak, req.text, voice, req.length_scale)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        log.info("seslendirildi (%s): %d harf → %.2f sn ses, %.2f sn", voice, len(req.text), len(x) / s.rate,
                 time.perf_counter() - t0)
        return Response(to_wav(x, s.rate), media_type="audio/wav")

    return app


def main() -> None:
    import uvicorn

    from sesli_asistan.libs.obs import setup_logging

    setup_logging("tts")
    s = TtsSettings()
    uvicorn.run(create_app(s), host=s.host, port=s.port, log_level="warning")
