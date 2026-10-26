"""TTS adaptörü: Piper HTTP servisi (tasarim.md 6.4, ilk sürüm).

Piper (piper1-gpl) GPL-3.0'dır; bu yüzden Gateway onu kütüphane olarak içermez, ayrı süreçteki HTTP
servisine istek atar (ADR-009, C-05).

Varsayılan istek biçimi piper1-gpl `python -m piper.http_server` ile aynıdır:
    POST /synthesize   {"text": "...", "voice": "...?", "length_scale": 1.0?}   →  audio/wav
Mevcut servisin (ilk şemadaki :5500) yolu ya da biçimi farklıysa `path` / `format` ayarıyla uyarlanır:
    format="json"   POST {path} JSON gövde (varsayılan)
    format="plain"  POST {path} düz metin gövde
    format="query"  GET  {path}?text=...
"""
from __future__ import annotations

import io
import wave
from typing import Literal

import httpx
import numpy as np

from sesli_asistan.gateway.core.ports import Audio, TtsError


def parse_wav(data: bytes) -> Audio:
    """16 bit PCM WAV → mono Audio. Çok kanallıysa kanalların ortalaması alınır."""
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise TtsError(f"WAV değil (ilk baytlar: {data[:12]!r})")
    try:
        with wave.open(io.BytesIO(data)) as w:
            if w.getsampwidth() != 2:
                raise TtsError(f"desteklenmeyen örnek genişliği: {w.getsampwidth() * 8} bit")
            ch, rate = w.getnchannels(), w.getframerate()
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    except (wave.Error, EOFError) as e:
        raise TtsError(f"bozuk WAV: {e}") from e
    if ch > 1:
        pcm = pcm[: len(pcm) // ch * ch].reshape(-1, ch).mean(axis=1).astype(np.int16)
    if len(pcm) == 0:
        raise TtsError("boş ses")
    return Audio(pcm=pcm, rate=rate)


class PiperHttpTts:
    def __init__(self, base_url: str = "http://127.0.0.1:5500", path: str = "/synthesize",
                 format: Literal["json", "plain", "query"] = "json", voice: str | None = None,
                 length_scale: float | None = None, timeout_s: float = 3.0, client: httpx.AsyncClient | None = None):
        self.path, self.format, self.voice, self.length_scale = path, format, voice, length_scale
        self.http = client or httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout_s)

    @property
    def cache_id(self) -> str:
        """Önbellek anahtarına girer: ses ya da hız değişince eski kayıtlar kullanılmaz."""
        return f"piper|{self.voice or '-'}|{self.length_scale or 1.0}"

    async def aclose(self) -> None:
        await self.http.aclose()

    async def synthesize(self, text: str) -> Audio:
        try:
            if self.format == "query":
                r = await self.http.get(self.path, params={"text": text})
            elif self.format == "plain":
                r = await self.http.post(self.path, content=text.encode("utf-8"),
                                         headers={"Content-Type": "text/plain; charset=utf-8"})
            else:
                body: dict = {"text": text}
                if self.voice:
                    body["voice"] = self.voice
                if self.length_scale:
                    body["length_scale"] = self.length_scale
                r = await self.http.post(self.path, json=body)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise TtsError(f"Piper: {e}") from e
        return parse_wav(r.content)
