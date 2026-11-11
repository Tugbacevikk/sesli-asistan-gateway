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
    # ADR-037: "muhasebeden ayşe hanım" kuralla çözülür, LLM hiç çağrılmaz (llm_latency 0). LLM ölçümü:
    # test_anlama_cagrisi_olculur
    assert lines["llm_latency_seconds_count"] == 0 and lines["tts_latency_seconds_count"] >= 1
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


def test_gunluk_kurulumu_bicim_duzey_ve_gizli_deger_suzgeci(monkeypatch):
    from sesli_asistan.libs.obs import RedactSecrets, setup_logging

    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    try:
        monkeypatch.setenv("LOG_FORMAT", "json")
        setup_logging("gateway", level="debug")
        (h,) = root.handlers
        assert isinstance(h.formatter, JsonFormatter) and root.level == logging.DEBUG
        assert any(isinstance(f, RedactSecrets) for f in h.filters)
        assert logging.getLogger("httpx").level == logging.WARNING      # gürültülü kütüphaneler susar
        setup_logging("stt", fmt="text", level="WARNING")
        assert not isinstance(root.handlers[0].formatter, JsonFormatter) and root.level == logging.WARNING
    finally:
        root.handlers[:] = saved[0]
        root.setLevel(saved[1])


async def test_anlama_cagrisi_olculur():
    """ADR-037: anlama kipinin `chat_json` çağrısı da ölçülür ve devre kesiciye girer (sarmalayıcılar
    `__getattr__` ile iç nesneye geçirseydi görünmez kalırdı)."""
    from sesli_asistan.gateway.core.resilience import BreakerLlm, CircuitBreaker, supports_json
    from sesli_asistan.gateway.metrics import GatewayMetrics, TimedLlm

    class Inner:
        async def chat_json(self, messages, max_tokens=80):
            return '{"niyet": "operator"}', "istem 1 token 1 ms"

    class Duz:
        async def chat(self, messages, tools):
            raise AssertionError("çağrılmamalı")

    m = GatewayMetrics()
    timed = TimedLlm(Inner(), m)
    assert supports_json(timed) and not supports_json(TimedLlm(Duz(), m))   # sarmalayıcı içteki adaptöre sorar
    before = m.registry.get_sample_value("llm_latency_seconds_count") or 0
    content, _ = await timed.chat_json([{"role": "user", "content": "merhaba"}])
    assert content.startswith("{")
    assert m.registry.get_sample_value("llm_latency_seconds_count") == before + 1

    breaker = CircuitBreaker("llm", failures=1, reset_s=60)
    assert (await BreakerLlm(Inner(), breaker).chat_json([]))[0].startswith("{")
