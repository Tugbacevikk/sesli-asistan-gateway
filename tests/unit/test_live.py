"""Canlı çağrı akışı (tasarim.md 12.5): İzleme'de süren çağrıların metni anında."""
import asyncio
import json
from contextlib import asynccontextmanager

from fastapi.testclient import TestClient
from test_ui import ROOT, Llm, Stt, Tts, recv_until

from sesli_asistan.gateway.api.access import UiAccess
from sesli_asistan.gateway.api.app import create_app
from sesli_asistan.gateway.composition import GatewayRuntime
from sesli_asistan.gateway.config import GatewaySettings
from sesli_asistan.gateway.core.manager import NewCall
from sesli_asistan.gateway.live import LiveFeed


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def test_olaylar_cagri_basina_tutulur_ve_biteni_silinir():
    clk = Clock()
    f = LiveFeed(clock=clk)
    on = f.observer("c1", "santral")
    on("state", {"state": "listening"})
    on("heard", {"text": "ayşe yılmaz"})
    on("reply", {"text": "Aktarıyorum."})
    on("turn", {"heard": "ayşe yılmaz", "reply": "Aktarıyorum.", "final_to_audio_s": 0.4, "llm_calls": 0})
    on("thinking_sound", {})                                   # arayüzün göstermediği olay: tutulmaz
    snap = f.snapshot()
    assert len(snap) == 1 and snap[0]["state"] == "listening" and not snap[0]["ended"]
    kinds = [e["type"] for e in snap[0]["events"]]
    assert kinds == ["start", "state", "heard", "reply", "turn"]
    assert "heard" not in snap[0]["events"][-1]                # tur olayı yalnız ölçüm taşır
    on("ended", {"outcome": "transferred"})
    clk.t = 29
    assert f.snapshot()[0]["ended"]
    clk.t = 31
    assert f.snapshot() == []                                  # biteli 30 sn: silindi


def test_metin_saklanmiyorsa_canli_akista_da_yok():
    f = LiveFeed(keep_texts=False)
    on = f.observer("c1", "santral")
    on("heard", {"text": "ayşe yılmaz"})
    on("reply", {"text": "Aktarıyorum."})
    ev = f.snapshot()[0]["events"]
    assert ev[1]["text"] == "" and ev[2]["text"] == ""


def test_yavas_abone_dusurulur_cagri_beklemez():
    async def go():
        f = LiveFeed()
        f.QUEUE = 3
        q = f.subscribe()
        on = f.observer("c1", "santral")
        for i in range(10):                                    # hiçbiri beklemez / hata vermez
            on("heard", {"text": str(i)})
        assert f.subscribers == 0 and q.get_nowait() is None   # kapat işareti
        assert len(f.snapshot()[0]["events"]) == 11            # olaylar yine tutulur
    asyncio.run(go())


def test_bitmeyen_cagri_sonsuza_kadar_kalmaz():
    clk = Clock()
    f = LiveFeed(clock=clk)
    f.observer("yarim", "santral")
    clk.t = f.STALE_S + 1
    assert f.snapshot() == []


def client():
    s = GatewaySettings(_env_file=None, directory_path=str(ROOT / "data/rehber_ornek.json"))
    rt = GatewayRuntime(s, llm=Llm(), stt=Stt(), tts=Tts())

    @asynccontextmanager
    async def lifespan(app):
        await rt.start()
        yield
        await rt.stop()

    app = create_app(rt.manager, rt.health, lifespan=lifespan, web_session=rt.web_session, settings=rt.settings,
                     ui_access=UiAccess("", {}, {"testclient"}), live=rt.live)
    return TestClient(app), rt


def test_izleme_baglantisi_deneme_cagrisini_canli_gorur():
    tc, rt = client()
    with tc, tc.websocket_connect("/v1/ui/canli") as live:
        assert live.receive_json() == {"type": "snapshot", "calls": []}
        with tc.websocket_connect("/v1/ui/ws?mod=metin") as ws:
            recv_until(ws, "state", state="listening")
            ws.send_text(json.dumps({"type": "text", "text": "muhasebeden ayşe hanım"}))
            recv_until(ws, "pbx_action")
            seen = []
            while not any(e["type"] == "action" for e in seen):
                seen.append(live.receive_json())
            ws.send_text(json.dumps({"type": "hangup"}))
        assert seen[0]["type"] == "start" and seen[0]["channel"] == "web-metin"
        assert next(e for e in seen if e["type"] == "heard")["text"] == "muhasebeden ayşe hanım"
        assert next(e for e in seen if e["type"] == "action")["extension"] == "214"
        while (e := live.receive_json())["type"] != "ended":
            pass
        assert e["call_id"] == seen[0]["call_id"]


def test_santral_cagrisi_canli_akisa_baglanir():
    s = GatewaySettings(_env_file=None, directory_path=str(ROOT / "data/rehber_ornek.json"))
    rt = GatewayRuntime(s, llm=Llm(), stt=Stt(), tts=Tts())
    rt.speaker = object()                                      # oturum kurmak için yeter (çalıştırılmıyor)

    class Media:
        pass

    sess = rt._new_session(NewCall("santral-1"), Media())
    sess._emit("heard", text="satış")
    ev = rt.live.snapshot()[0]
    assert ev["call_id"] == "santral-1" and ev["channel"] == "santral"
    assert [e["type"] for e in ev["events"]][0] == "start" and ev["events"][-1]["text"] == "satış"
    rt.history.close()


def test_anahtarsiz_ve_baska_siteden_baglanti_reddedilir():
    s = GatewaySettings(_env_file=None, directory_path=str(ROOT / "data/rehber_ornek.json"))
    rt = GatewayRuntime(s, llm=Llm(), stt=Stt(), tts=Tts())
    app = create_app(rt.manager, rt.health, web_session=rt.web_session, settings=rt.settings,
                     ui_access=UiAccess("gizli-anahtar-1234567", {}, {"testclient"}), live=rt.live)
    import pytest
    from starlette.websockets import WebSocketDisconnect

    tc = TestClient(app)
    with pytest.raises(WebSocketDisconnect), tc.websocket_connect("/v1/ui/canli") as ws:
        ws.receive_json()
    tc.cookies.set("sesli_oturum", "gizli-anahtar-1234567")
    with pytest.raises(WebSocketDisconnect), tc.websocket_connect(
            "/v1/ui/canli", headers={"origin": "https://kotu.example"}) as ws:
        ws.receive_json()
    with tc.websocket_connect("/v1/ui/canli") as ws:
        assert ws.receive_json()["type"] == "snapshot"
    rt.history.close()
