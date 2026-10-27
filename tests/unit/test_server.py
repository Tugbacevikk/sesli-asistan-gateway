"""Servis başlatma: `python -m sesli_asistan.gateway` yolundaki gibi, uygulama olay döngüsü başlamadan kurulur."""
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from sesli_asistan.gateway.composition import GatewayRuntime
from sesli_asistan.gateway.config import GatewaySettings
from sesli_asistan.gateway.core.ports import Audio
from sesli_asistan.gateway.server import build_app

ROOT = Path(__file__).resolve().parents[2]


class Tts:
    async def synthesize(self, text):
        return Audio(np.zeros(800, np.int16), 8000)


class Llm:
    async def chat(self, messages, tools):
        raise AssertionError("çağrılmamalı")


def test_dongu_baslamadan_kurulan_uygulama_acilir():
    # Hata (Antigravity, 27.10.2026): gRPC kanalı uvicorn döngüsünden önce açılıyordu →
    # "attached to a different loop", servis hiç açılmadı. Burada da GatewayRuntime döngü yokken kurulur.
    s = GatewaySettings(_env_file=None, directory_path=str(ROOT / "data/rehber_ornek.json"),
                        stt_grpc_addr="127.0.0.1:1")                 # STT yok
    rt = GatewayRuntime(s, llm=Llm(), tts=Tts())                    # gerçek GrpcStt + PbxRestClient
    with TestClient(build_app(s, rt)) as client:
        h = client.get("/v1/health")
        assert h.status_code == 503 and h.json()["components"]["stt"] == "down"
        assert h.json()["components"]["directory"] == "ok" and h.json()["components"]["tts"] == "ok"
        r = client.post("/v1/calls", json={"call_id": "a"})
        assert r.status_code == 503 and r.json()["error"]["code"] == "UNAVAILABLE"
