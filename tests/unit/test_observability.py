"""Gözlemlenebilirlik (Aşama 6): JSON log ve Prometheus metrikleri."""
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from sesli_asistan.gateway.api.app import create_app
from sesli_asistan.gateway.composition import GatewayRuntime
from sesli_asistan.gateway.config import GatewaySettings
from sesli_asistan.libs.obs import JsonFormatter
from tests.unit.test_ui import Llm, Stt, Tts, recv_until

ROOT = Path(__file__).resolve().parents[2]


def test_json_log_cagri_kimligini_ayirir():
    f = JsonFormatter("gateway")
    rec = logging.LogRecord("sesli_asistan.gateway.core.call", logging.INFO, "", 0, "[c-42] tur: %r", ("ayşe",), None)
    out = json.loads(f.format(rec))
    assert out["service"] == "gateway" and out["call_id"] == "c-42" and out["msg"] == "tur: 'ayşe'"
    assert out["level"] == "INFO" and "T" in out["ts"]
    rec = logging.LogRecord("x", logging.WARNING, "", 0, "rehber güncellenemedi", (), None)
    assert json.loads(f.format(rec))["call_id"] is None


def test_metrikler_cagridan_sonra():
    s = GatewaySettings(_env_file=None, directory_path=str(ROOT / "data/rehber_ornek.json"))
    rt = GatewayRuntime(s, llm=Llm(), stt=Stt(), tts=Tts())

    @asynccontextmanager
    async def lifespan(app):
        await rt.start()
        yield
        await rt.stop()

    app = create_app(rt.manager, rt.health, lifespan=lifespan, web_session=rt.web_session,
                     metrics_registry=rt.metrics.registry)
    with TestClient(app) as tc, tc.websocket_connect("/v1/ui/ws") as ws:
        recv_until(ws, "call")
        for _ in range(40):
            ws.send_bytes(np.zeros(160, "<i2").tobytes())
        recv_until(ws, "pbx_action")
        ws.send_text(json.dumps({"type": "transfer_result", "status": "answered"}))
        recv_until(ws, "state", state="handed_off")
        ws.send_text(json.dumps({"type": "hangup"}))
        recv_until(ws, "summary")
        text = tc.get("/metrics").text
    lines = {ln.split(" ")[0]: float(ln.split(" ")[1]) for ln in text.splitlines() if ln and not ln.startswith("#")}
    assert lines['calls_total{channel="web",outcome="transferred"}'] == 1
    assert lines['actions_total{type="transfer"}'] == 1
    assert lines['turn_latency_seconds_count{channel="web"}'] == 1
    assert lines["llm_latency_seconds_count"] >= 1 and lines["tts_latency_seconds_count"] >= 1
    assert lines['dependency_up{service="stt"}'] == 1 and lines["calls_active"] == 0
    assert lines["directory_entries"] == 10 and lines["transfer_policy_rejections_total"] == 0


def test_tur_yolu_metrigi():
    from sesli_asistan.gateway.metrics import GatewayMetrics
    m = GatewayMetrics()
    on = m.observer("santral")
    on("turn", {"heard": "ayşe yılmaz", "llm_calls": 0, "final_to_audio_s": 0.1, "dialog_s": 0.0})
    on("turn", {"heard": "ferhat", "llm_calls": 2, "final_to_audio_s": 2.0, "dialog_s": 1.9})
    on("turn", {"heard": "[santral: busy]", "llm_calls": 0, "final_to_audio_s": None, "dialog_s": 0.0})
    g = m.registry.get_sample_value
    assert g("dialog_turns_total", {"yol": "kod"}) == 1 and g("dialog_turns_total", {"yol": "llm"}) == 1
