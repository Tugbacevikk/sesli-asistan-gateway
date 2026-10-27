"""Kadın sesli TTS servisi (ADR-015): Piper ile aynı HTTP sözleşmesi; Gateway'in Piper adaptörüyle uçtan uca."""
import io
import wave

import httpx
import numpy as np
from fastapi.testclient import TestClient

from sesli_asistan.gateway.adapters.tts.piper_http import PiperHttpTts
from sesli_asistan.tts.config import TtsSettings
from sesli_asistan.tts.server import create_app


class FakeSeslen:
    sesler = {"ayse": {}, "zeynep": {}}

    def __init__(self):
        self.calls = []

    def tum(self, metin, **ayar):
        self.calls.append((metin, ayar))
        t = np.arange(int(24000 * 0.5)) / 24000
        return (0.2 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def test_piper_sozlesmesi_ve_kadin_sesi():
    eng = FakeSeslen()
    with TestClient(create_app(TtsSettings(_env_file=None), eng)) as c:
        assert c.get("/").json() == {"status": "ok", "voice": "ayse", "voices": ["ayse", "zeynep"]}
        r = c.post("/synthesize", json={"text": "Sizi Ayşe Yılmaz'a aktarıyorum.", "length_scale": 1.25})
        assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
        with wave.open(io.BytesIO(r.content)) as w:
            assert w.getframerate() == 24000 and w.getnchannels() == 1 and w.getnframes() == 12000
        assert eng.calls[0] == ("Sizi Ayşe Yılmaz'a aktarıyorum.", {"ses": "ayse", "ton": "notr", "hiz": 0.8})
        assert c.post("/synthesize", json={"text": "x", "voice": "yok"}).status_code == 400
        assert c.post("/synthesize", json={"text": ""}).status_code == 422


def test_bilinmeyen_varsayilan_ses_servisi_baslatmaz():
    import pytest

    with pytest.raises(RuntimeError, match="bilinmeyen ses"):
        with TestClient(create_app(TtsSettings(_env_file=None, voice="ahmet"), FakeSeslen())):
            pass


async def test_gateway_piper_adaptoru_ile_calisir():
    app = create_app(TtsSettings(_env_file=None), FakeSeslen())
    async with app.router.lifespan_context(app):
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://tts")
        audio = await PiperHttpTts("http://tts", client=client).synthesize("Merhaba.")
        assert audio.rate == 24000 and abs(audio.seconds - 0.5) < 0.01
