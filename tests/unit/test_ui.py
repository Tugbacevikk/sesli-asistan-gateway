"""Arayüz (ADR-014): WebSocket deneme çağrısı, izleme uçları, erişim kuralları. Gerçek tarayıcı testi: README."""
import asyncio
import json
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from sesli_asistan.gateway.api.app import create_app
from sesli_asistan.gateway.composition import GatewayRuntime
from sesli_asistan.gateway.config import GatewaySettings
from sesli_asistan.gateway.core.ports import Audio, LlmMessage, SttEvent, ToolCall

ROOT = Path(__file__).resolve().parents[2]


class Stt:
    """30 kare (0,6 sn) ses gelince arayan "muhasebeden ayşe hanım" demiş olur."""

    async def open(self, call_id, codec, hotwords):
        return Stream()


class Stream:
    def __init__(self):
        self.n, self.q = 0, asyncio.Queue()

    async def send(self, frame):
        assert len(frame) == 160          # 20 ms G.711: tarayıcının PCM16'sı telefon kalitesine indirildi
        self.n += 1
        if self.n == 30:
            self.q.put_nowait(SttEvent("speech_started"))
            self.q.put_nowait(SttEvent("final", "muhasebeden ayşe hanım", 0.9, 0, 1500))

    async def events(self):
        while (e := await self.q.get()) is not None:
            yield e

    def cancel(self):
        self.q.put_nowait(None)


class Llm:
    async def chat(self, messages, tools):
        return LlmMessage("", [ToolCall("c", "InContacts", {"Name": "Ayşe", "Department": "muhasebe"})])


class Tts:
    async def synthesize(self, text):
        return Audio(np.zeros(800, np.int16), 8000)


def client(ui=True, ui_ips=None, key=""):
    s = GatewaySettings(_env_file=None, directory_path=str(ROOT / "data/rehber_ornek.json"))
    rt = GatewayRuntime(s, llm=Llm(), stt=Stt(), tts=Tts())

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app):
        await rt.start()
        yield
        await rt.stop()

    app = create_app(rt.manager, rt.health, api_key=key, lifespan=lifespan,
                     web_session=rt.web_session if ui else None, ui_allowed_ips=ui_ips, settings=rt.settings)
    return TestClient(app), rt


def recv_until(ws, kind, **match):
    seen = []
    while True:
        m = ws.receive()
        if m.get("text"):
            ev = json.loads(m["text"])
            seen.append(ev)
            if ev["type"] == kind and all(ev.get(k) == v for k, v in match.items()):
                return ev, seen


def test_tarayicidan_deneme_cagrisi_bastan_sona():
    tc, rt = client()
    with tc, tc.websocket_connect("/v1/ui/ws") as ws:
        call, _ = recv_until(ws, "call")
        assert call["call_id"].startswith("web-")
        for _ in range(40):
            ws.send_bytes(np.zeros(160, "<i2").tobytes())
        act, seen = recv_until(ws, "pbx_action")
        assert act["action"] == "transfer" and act["extension"] == "214"
        kinds = [e["type"] for e in seen]
        assert "heard" in kinds and "reply" in kinds and "turn" in kinds
        assert next(e for e in seen if e["type"] == "heard")["text"] == "muhasebeden ayşe hanım"
        ws.send_text(json.dumps({"type": "transfer_result", "status": "answered"}))
        recv_until(ws, "state", state="handed_off")     # arayüz bunu görünce çağrıyı kapatır
        ws.send_text(json.dumps({"type": "hangup"}))
        summary, seen = recv_until(ws, "summary")
        assert any(e["type"] == "ended" and e["outcome"] == "transferred" for e in seen)
        assert summary["channel"] == "web" and summary["turns"][0]["actions"] == ["transfer:214"]

        calls = tc.get("/v1/admin/calls").json()
        assert calls["recent"][0]["call_id"] == call["call_id"] and calls["active"] == []
        assert tc.get("/v1/admin/health").json()["status"] == "ok"
        page = tc.get("/ui")
        assert page.status_code == 200 and "Deneme çağrısı" in page.text


def test_arayuz_kapali_ve_erisim_kurallari():
    tc, _ = client(ui=False)
    with tc:
        assert tc.get("/ui").status_code == 404
    tc, _ = client(ui_ips={"10.1.1.1"})
    with tc:
        assert tc.get("/ui").status_code == 401
        assert tc.get("/v1/admin/calls").status_code == 401
    tc, _ = client(key="gizli")
    with tc:
        assert tc.get("/ui").status_code == 401
        assert tc.get("/ui?anahtar=gizli").status_code == 200
        assert tc.get("/v1/admin/calls", headers={"Authorization": "Bearer gizli"}).status_code == 200


def test_bulunamayan_aramalar_listelenir():
    tc, rt = client()

    class Miss:
        async def chat(self, messages, tools):
            return LlmMessage("", [ToolCall("c", "InContacts", {"Name": "Ferhat Yıldırım"})]) \
                if messages[-1]["role"] == "user" else LlmMessage("Bulamadım, tekrar söyler misiniz?")

    rt.agent.llm = Miss()
    with tc, tc.websocket_connect("/v1/ui/ws") as ws:
        recv_until(ws, "call")
        for _ in range(40):
            ws.send_bytes(np.zeros(160, "<i2").tobytes())
        recv_until(ws, "turn")
        ws.send_text(json.dumps({"type": "hangup"}))
        recv_until(ws, "summary")
        misses = tc.get("/v1/admin/calls").json()["misses"]
    assert misses == [{"text": "ferhat yıldırım", "count": 1}]


def test_yazarak_deneme():
    # Mikrofon yok: arayan yazıyor; ses gelmediği için çağrı "ses gelmedi" diye kapanmamalı
    tc, rt = client()
    with tc, tc.websocket_connect("/v1/ui/ws?mod=metin") as ws:
        recv_until(ws, "state", state="listening")
        ws.send_text(json.dumps({"type": "text", "text": "muhasebeden ayşe hanım"}))
        act, seen = recv_until(ws, "pbx_action")
        assert act["extension"] == "214"
        assert next(e for e in seen if e["type"] == "heard")["text"] == "muhasebeden ayşe hanım"
        assert rt.manager.sessions[next(iter(rt.manager.sessions))].media.idle_s() == 0
        ws.send_text(json.dumps({"type": "hangup"}))
        summary, _ = recv_until(ws, "summary")
    assert summary["channel"] == "web-metin" and summary["turns"][0]["heard"] == "muhasebeden ayşe hanım"


def test_mesai_durumu_arayuzden_zorlanir_ve_saglikta_gorunur():
    tc, rt = client()
    with tc, tc.websocket_connect("/v1/ui/ws?mod=metin&mesai=tatil") as ws:
        act, seen = recv_until(ws, "pbx_action")
        assert act["action"] == "hangup"
        assert next(e for e in seen if e["type"] == "reply")["text"].startswith("Bugün tatil")
        ws.send_text(json.dumps({"type": "hangup"}))
        summary, _ = recv_until(ws, "summary")
        assert summary["outcome"] == "closed" and summary["reason"] == "tatil"
        assert tc.get("/v1/admin/health").json()["office"] == "acik"     # kural yok: hep açık
