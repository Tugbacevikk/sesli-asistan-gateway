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


def test_guvenlik_uyarilari():
    from sesli_asistan.gateway.config import GatewaySettings
    from sesli_asistan.gateway.server import security_warnings
    w = security_warnings(GatewaySettings(_env_file=None, ui_allowed_ips="*"))
    assert len(w) == 3
    ok = GatewaySettings(_env_file=None, api_key="x" * 20, pbx_allowed_ips="10.0.0.5")
    assert security_warnings(ok) == []


def test_acilis_guvenlik_uyarilarini_loglar_ve_uvicornu_ayarlarla_baslatir(monkeypatch, tmp_path, caplog):
    import logging

    import sesli_asistan.gateway.server as srv

    seen = {}
    monkeypatch.setattr(srv, "setup_logging", lambda name: None)
    monkeypatch.setattr(srv, "build_app", lambda s: "app")
    monkeypatch.setattr(srv.uvicorn, "run", lambda app, **kw: seen.update(app=app, **kw))
    monkeypatch.setenv("GATEWAY_API_HOST", "0.0.0.0")
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "127.0.0.1")
    monkeypatch.setenv("LOCAL_SETTINGS_FILE", str(tmp_path / "yerel.json"))
    with caplog.at_level(logging.INFO):
        srv.main()
    assert seen["app"] == "app" and seen["host"] == "0.0.0.0" and seen["proxy_headers"] is True
    assert seen["forwarded_allow_ips"] == "127.0.0.1"
    assert any("GÜVENLİK" in r.message for r in caplog.records)          # deneme ayarları: anahtar yok
