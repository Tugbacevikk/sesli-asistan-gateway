"""Uçtan uca (Aşama 5): santral gibi REST + gerçek UDP/RTP → Gateway → gerçek gRPC STT servisi (sahte Whisper,
enerji VAD) → diyalog (sahte LLM, gerçek rehber araması) → TTS (sahte) → RTP cevabı → santrale aktarma eylemi.

Ağ: yalnızca 127.0.0.1. Model gerekmez.
"""
import asyncio
import socket
from pathlib import Path

import httpx
import numpy as np
import pytest

from sesli_asistan.gateway.adapters.stt.grpc_client import GrpcStt
from sesli_asistan.gateway.api.app import create_app
from sesli_asistan.gateway.composition import GatewayRuntime
from sesli_asistan.gateway.config import GatewaySettings
from sesli_asistan.gateway.core.ports import Audio, LlmMessage, ToolCall
from sesli_asistan.libs.audio import g711
from sesli_asistan.stt.config import SttSettings
from sesli_asistan.stt.engine import SttResult
from sesli_asistan.stt.server import start_server

ROOT = Path(__file__).resolve().parents[2]
KEY = "gizli-anahtar"


class Whisper:
    def __init__(self, text):
        self.text, self.hotwords = text, None

    def transcribe(self, samples, hotwords=None):
        self.hotwords = hotwords
        return SttResult(self.text, 0.9)


class Llm:
    def __init__(self, *replies):
        self.replies = list(replies)

    async def chat(self, messages, tools):
        return self.replies.pop(0)


class Tts:
    async def synthesize(self, text):
        t = np.arange(1600) / 8000
        return Audio((3000 * np.sin(2 * np.pi * 440 * t)).astype(np.int16), 8000)


class Pbx:
    def __init__(self):
        self.actions = []

    async def send_action(self, call_id, action):
        self.actions.append((call_id, action))


def rtp_frames(*parts):
    """(saniye, ses var mı) → 20 ms PCMA RTP paketleri."""
    x = []
    for sec, voiced in parts:
        n = int(sec * 8000)
        t = np.arange(n) / 8000
        x.append(0.3 * np.sin(2 * np.pi * 300 * t) if voiced else np.zeros(n))
    payload = g711.encode(np.concatenate(x).astype(np.float32), "pcma")
    for i, k in enumerate(range(0, len(payload), 160)):
        yield (bytes([0x80, 8 | (0x80 if i == 0 else 0)]) + (i & 0xFFFF).to_bytes(2, "big")
               + (k & 0xFFFFFFFF).to_bytes(4, "big") + (0x1234).to_bytes(4, "big") + payload[k:k + 160])


@pytest.fixture
async def gateway():
    made = []

    async def make(stt_text, *llm_replies, **settings):
        whisper = Whisper(stt_text)
        srv, _, port = await start_server(SttSettings(vad="energy"), whisper, bind="127.0.0.1:0")
        conf = {"directory_path": str(ROOT / "data/rehber_ornek.json"), "api_key": KEY, "rtp_host": "127.0.0.1",
                "rtp_port_min": 31000, "rtp_port_max": 31099, "pbx_allowed_ips": "127.0.0.1", **settings}
        s = GatewaySettings(_env_file=None, **conf)
        pbx = Pbx()
        rt = GatewayRuntime(s, llm=Llm(*llm_replies), stt=GrpcStt(f"127.0.0.1:{port}"), pbx=pbx, tts=Tts())
        await rt.start()
        app = create_app(rt.manager, rt.health, api_key=KEY, allowed_ips={"127.0.0.1"})
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 5000)),
                                   base_url="http://gw", headers={"Authorization": f"Bearer {KEY}"})
        made.append((srv, rt, client))
        return rt, client, pbx, whisper
    yield make
    for srv, rt, client in made:
        await client.aclose()
        await rt.stop()
        await srv.stop(0)


async def until(pred, timeout=5.0):
    async def wait():
        while not pred():
            await asyncio.sleep(0.01)
    await asyncio.wait_for(wait(), timeout)


async def test_cagri_bastan_sona(gateway):
    search = ToolCall("c1", "InContacts", {"Name": "Ayşe", "Department": "muhasebe"})
    rt, api, pbx, whisper = await gateway("muhasebeden ayşe hanım", LlmMessage("", [search]))
    assert (await api.get("/v1/health")).json()["status"] == "ok"

    pbx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)   # santralin ses portu
    pbx_sock.bind(("127.0.0.1", 0))
    pbx_sock.setblocking(False)
    host, pbx_port = pbx_sock.getsockname()
    r = await api.post("/v1/calls", json={"call_id": "c-1", "caller": "05321234567", "called": "4440000",
                                          "codec": "pcma", "rtp_remote": {"host": host, "port": pbx_port}})
    assert r.status_code == 201
    rtp_port = r.json()["rtp_port"]
    assert 31000 <= rtp_port <= 31099

    loop = asyncio.get_running_loop()
    first = await asyncio.wait_for(loop.sock_recv(pbx_sock, 2048), 3)          # karşılama sesi geldi
    assert first[1] & 0x7F == 8 and first[1] & 0x80                             # PCMA, konuşma başı işareti

    for pkt in rtp_frames((0.3, False), (1.0, True), (1.0, False)):            # arayan konuşuyor
        pbx_sock.sendto(pkt, ("127.0.0.1", rtp_port))
    await until(lambda: pbx.actions)
    call_id, action = pbx.actions[0]
    assert call_id == "c-1" and action["type"] == "transfer" and action["extension"] == "214"
    assert "Ayşe Yılmaz" in whisper.hotwords                                    # rehber ipuçları STT'ye gitti

    r = await api.post("/v1/calls/c-1/events", json={"type": "transfer_result", "status": "answered"})
    assert r.status_code == 202
    assert (await api.request("DELETE", "/v1/calls/c-1", json={"reason": "transferred"})).status_code == 204
    await until(lambda: rt.manager.results)
    _, stats = rt.manager.results[0]
    assert stats.outcome == "transferred" and stats.rtp["paket"] >= 100 and stats.rtp["gonderilen"] > 10
    t = stats.turns[0]
    assert t.heard == "muhasebeden ayşe hanım" and t.actions == ["transfer:214"] and t.final_to_audio_s < 1.0
    pbx_sock.close()


async def test_api_kurallari(gateway):
    rt, api, pbx, _ = await gateway("x", max_concurrent_calls=1, rtp_port_max=31001)
    no_auth = await api.post("/v1/calls", json={"call_id": "a"}, headers={"Authorization": "Bearer yanlis"})
    assert no_auth.status_code == 401 and no_auth.json()["error"]["code"] == "UNAUTHORIZED"
    bad = await api.post("/v1/calls", json={"call_id": "a", "codec": "opus"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "BAD_REQUEST"
    assert (await api.post("/v1/calls", json={"call_id": "a"})).status_code == 201
    dup = await api.post("/v1/calls", json={"call_id": "a"})
    assert dup.status_code == 409 and dup.json()["error"] == {"code": "CALL_EXISTS", "message": "açık oturum var: a",
                                                             "call_id": "a"}
    full = await api.post("/v1/calls", json={"call_id": "b"})
    assert full.status_code == 503
    ev = await api.post("/v1/calls/yok/events", json={"type": "dtmf", "digit": "1"})
    assert ev.status_code == 404 and ev.json()["error"]["call_id"] == "yok"
    assert (await api.post("/v1/calls/a/events", json={"type": "dtmf", "digit": "12"})).status_code == 400
    assert (await api.delete("/v1/calls/a")).status_code == 204
    assert (await api.delete("/v1/calls/a")).status_code == 204                 # idempotent
    assert (await api.get("/v1/openapi.json", headers={})).status_code == 200


async def test_hazir_degilken_cagri_reddedilir(gateway):
    rt, api, _, _ = await gateway("x")
    rt.components["stt"] = "down"
    h = await api.get("/v1/health")
    assert h.status_code == 503 and h.json()["components"]["stt"] == "down"
    assert (await api.post("/v1/calls", json={"call_id": "a"})).status_code == 503


async def test_izinsiz_kaynak_reddedilir():
    from sesli_asistan.gateway.core.manager import CallManager, PortPool

    m = CallManager(None, None, PortPool(1, 2))
    app = create_app(m, lambda: {"status": "ok"}, allowed_ips={"10.0.0.5"})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("10.9.9.9", 1)),
                                 base_url="http://gw") as c:
        r = await c.get("/v1/health")
    assert r.status_code == 401 and r.json()["error"]["code"] == "FORBIDDEN_SOURCE"


async def test_bakim_bosaltma_acik_cagriyi_kesmez(gateway):
    search = ToolCall("c1", "InContacts", {"Name": "Ayşe", "Department": "muhasebe"})
    rt, api, pbx, _ = await gateway("muhasebeden ayşe hanım", LlmMessage("", [search]))
    r = await api.post("/v1/calls", json={"call_id": "c-9", "caller": "1", "called": "2", "codec": "pcma",
                                          "rtp_remote": {"host": "127.0.0.1", "port": 9}})
    assert r.status_code == 201
    drain = asyncio.create_task(api.post("/v1/yerel/bosalt", json={"bekle_sn": 5}))
    await until(lambda: rt.manager.draining)
    h = await api.get("/v1/health")
    assert h.status_code == 503 and h.json()["status"] == "draining"      # santral yeni çağrıyı operatöre verir
    r = await api.post("/v1/calls", json={"call_id": "c-10", "caller": "1", "called": "2", "codec": "pcma"})
    assert r.status_code == 503 and "boşaltılıyor" in r.json()["error"]["message"]
    assert not drain.done()                                               # açık çağrı bekleniyor, kesilmedi
    await api.request("DELETE", "/v1/calls/c-9", json={"reason": "hangup"})
    r = await asyncio.wait_for(drain, 5)
    assert r.json() == {"draining": True, "active": 0}
    r = await api.post("/v1/yerel/bosalt", json={"iptal": True})
    assert r.json()["draining"] is False and (await api.get("/v1/health")).json()["status"] == "ok"


async def test_bosaltma_yalniz_sunucunun_kendisinden(gateway):
    rt, api, _, _ = await gateway("x")
    assert (await api.post("/v1/yerel/bosalt", json={"bekle_sn": 0},
                           headers={"X-Forwarded-For": "10.1.2.3"})).status_code == 403   # vekil üzerinden
    app = create_app(rt.manager, rt.health, api_key=KEY, allowed_ips={"10.9.9.9"})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("10.9.9.9", 1)),
                                 base_url="http://gw") as c:
        assert (await c.post("/v1/yerel/bosalt", json={})).status_code == 403
    assert not rt.manager.draining


async def test_buyuk_govde_reddedilir(gateway):
    _, api, _, _ = await gateway("x")
    r = await api.post("/v1/calls", content=b"{" + b" " * (600 * 1024) + b"}",
                       headers={"Content-Type": "application/json"})
    assert r.status_code == 413 and r.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


async def test_rtp_hedefi_sonradan_bildirilebilir(gateway):
    rt, api, _, _ = await gateway("x")
    r = await api.post("/v1/calls", json={"call_id": "c-r", "caller": "1", "called": "2", "codec": "pcma"})
    assert r.status_code == 201
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.setblocking(False)
    port = sock.getsockname()[1]
    r = await api.post("/v1/calls/c-r/events", json={"type": "rtp_remote", "rtp_remote": {"host": "127.0.0.1",
                                                                                        "port": port}})
    assert r.status_code == 202
    data = await asyncio.wait_for(asyncio.get_running_loop().sock_recv(sock, 2048), 3)   # karşılama, paket beklemeden
    assert data[1] & 0x7F == 8
    bad = await api.post("/v1/calls/c-r/events", json={"type": "rtp_remote", "rtp_remote": {"host": "", "port": 1}})
    assert bad.status_code == 400
    await api.request("DELETE", "/v1/calls/c-r", json={"reason": "hangup"})
    sock.close()
