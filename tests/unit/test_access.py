"""Arayüz rolleri (yönetici / izleyici) ve değişiklik geçmişi."""
import json
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient
from test_ui import ROOT, Llm, Stt, Tts

from sesli_asistan.gateway.api.access import ADMIN, VIEWER, UiAccess, parse_users
from sesli_asistan.gateway.api.app import create_app
from sesli_asistan.gateway.composition import GatewayRuntime
from sesli_asistan.gateway.config import GatewaySettings

ADMIN_KEY, VIEWER_KEY, API_KEY = "yonetici-anahtari-123456", "izleyici-anahtari-123456", "santral-anahtari"
JSON = {"Content-Type": "application/json"}


def client(users=f"tugba:yonetici:{ADMIN_KEY}, ali:izleyici:{VIEWER_KEY}"):
    s = GatewaySettings(_env_file=None, directory_path=str(ROOT / "data/rehber_ornek.json"), ui_users=users,
                        api_key=API_KEY)
    rt = GatewayRuntime(s, llm=Llm(), stt=Stt(), tts=Tts())

    @asynccontextmanager
    async def lifespan(app):
        await rt.start()
        yield
        await rt.stop()

    app = create_app(rt.manager, rt.health, api_key=API_KEY, lifespan=lifespan, web_session=rt.web_session,
                     settings=rt.settings, history=rt.history, metrics_registry=rt.metrics.registry,
                     ui_access=UiAccess(API_KEY, s.users(), {"testclient"}))
    return TestClient(app), rt


def auth(key):
    return {"Authorization": f"Bearer {key}"}


def test_izleyici_gorur_degistiremez_yonetici_degistirir_gecmise_yazilir():
    tc, rt = client()
    with tc:
        v = tc.get("/v1/admin/settings", headers=auth(VIEWER_KEY)).json()
        assert v["writable"] is False and v["user"] == {"name": "ali", "role": VIEWER}
        body = json.dumps({"aliases": {"c-101": ["Ayşe Abla"]}})
        r = tc.put("/v1/admin/settings", content=body, headers={**auth(VIEWER_KEY), **JSON})
        assert r.status_code == 403
        assert tc.post("/v1/admin/contacts", content="{}", headers={**auth(VIEWER_KEY), **JSON}).status_code == 403
        assert tc.delete("/v1/admin/contacts/yerel-00000000", headers=auth(VIEWER_KEY)).status_code == 403
        # santral anahtarı arayüzde yalnız izleyici: metrikleri okur, ayar değiştiremez
        assert tc.get("/metrics", headers=auth(API_KEY)).status_code == 200
        assert tc.put("/v1/admin/settings", content=body, headers={**auth(API_KEY), **JSON}).status_code == 403
        assert tc.get("/v1/admin/settings", headers=auth("yanlis-anahtar-0000000")).status_code == 401

        r = tc.put("/v1/admin/settings", content=body, headers={**auth(ADMIN_KEY), **JSON})
        assert r.status_code == 200 and r.json()["writable"] and r.json()["user"]["name"] == "tugba"
        r = tc.post("/v1/admin/contacts", content=json.dumps({"first_name": "Ferhat", "last_name": "Yıldırım",
                                                              "department": "Bilgi İşlem", "extension": "246"}),
                    headers={**auth(ADMIN_KEY), **JSON})
        assert r.status_code == 201
        ch = tc.get("/v1/admin/changes", headers=auth(VIEWER_KEY)).json()["changes"]
        assert [c["kim"] for c in ch] == ["tugba", "tugba"] and ch[0]["rol"] == ADMIN
        assert ch[0]["ayrinti"] == ["yerel kayıt eklendi: Ferhat Yıldırım (Bilgi İşlem, 246)"]
        assert ch[1]["ayrinti"] == ["takma ad eklendi: Ayşe Yılmaz ← Ayşe Abla"] and ch[1]["islem"] == "ayarlar"
        # değişiklik olmayan kayıt geçmişe yazılmaz
        tc.put("/v1/admin/settings", content=body, headers={**auth(ADMIN_KEY), **JSON})
        assert len(tc.get("/v1/admin/changes", headers=auth(ADMIN_KEY)).json()["changes"]) == 2


def test_websocket_rolu_izleyici_de_deneme_yapabilir():
    tc, _ = client()
    with tc, tc.websocket_connect(f"/v1/ui/ws?mod=metin&anahtar={VIEWER_KEY}") as ws:
        assert json.loads(ws.receive_text())["type"] in ("call", "state")


def test_kullanici_tanimi_yoksa_eski_davranis():
    a = UiAccess("anahtar", {}, None)
    assert a.check("h", "anahtar")[0].role == ADMIN and a.check("h", "baska")[0] is None
    assert UiAccess("", {}, None).check("h", "")[0].role == ADMIN          # geliştirme: anahtarsız yerel
    assert UiAccess("", {}, {"127.0.0.1"}).check("10.0.0.5", "")[1].startswith("kaynak adres")


@pytest.mark.parametrize("spec,msg", [
    ("tugba:yonetici", "ad:rol:anahtar"), ("tugba:patron:" + "x" * 16, "rol"), ("tugba:yonetici:kisa", "16"),
    (":yonetici:" + "x" * 16, "ad boş"), (f"a:yonetici:{'x' * 16}, b:izleyici:{'x' * 16}", "aynı anahtar"),
])
def test_hatali_kullanici_tanimi(spec, msg):
    with pytest.raises(ValueError, match=msg):
        parse_users(spec)
    with pytest.raises(ValueError):
        GatewaySettings(_env_file=None, ui_users=spec)


def test_kalici_degisiklik_gecmisi(tmp_path):
    from sesli_asistan.gateway.adapters.history.sqlite import SqliteCallHistory
    h = SqliteCallHistory(str(tmp_path / "g.db"))
    h.change_add("tugba", ADMIN, "10.0.0.5", "ayarlar", ["Karşılama anonsu değişti: Merhaba"])
    h.close()
    h = SqliteCallHistory(str(tmp_path / "g.db"))
    c = h.changes()
    assert c[0]["kim"] == "tugba" and c[0]["ayrinti"] == ["Karşılama anonsu değişti: Merhaba"]
    h.close()


def test_anahtar_adresten_cereze_cevrilir_ve_loglanmaz(caplog):
    import logging

    from sesli_asistan.libs.obs import RedactSecrets
    tc, _ = client()
    with tc:
        r = tc.get(f"/ui?anahtar={ADMIN_KEY}", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/ui"
        ck = r.headers["set-cookie"]
        assert "HttpOnly" in ck and "SameSite=strict" in ck.replace("Strict", "strict")
        # çerezle: sayfa, ayarlar ve rol
        assert tc.get("/ui").status_code == 200
        assert tc.get("/v1/admin/settings").json()["user"]["name"] == "tugba"
    rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "GET %s" 200',
                            ("1.2.3.4", f"/ui?anahtar={ADMIN_KEY}&x=1"), None)
    RedactSecrets().filter(rec)
    assert ADMIN_KEY not in rec.getMessage() and "anahtar=***&x=1" in rec.getMessage()


def test_baska_siteden_websocket_reddedilir():
    import pytest as _p
    from starlette.websockets import WebSocketDisconnect
    tc, _ = client()
    with tc:
        tc.cookies.set("sesli_oturum", ADMIN_KEY)
        with _p.raises(WebSocketDisconnect), tc.websocket_connect(
                "/v1/ui/ws?mod=metin", headers={"origin": "https://kotu.example"}) as ws:
            ws.receive_text()
        with tc.websocket_connect("/v1/ui/ws?mod=metin", headers={"origin": "http://testserver"}) as ws:
            assert json.loads(ws.receive_text())["type"] in ("call", "state")


def test_log_metinleri_gizlenebilir(monkeypatch):
    from sesli_asistan.libs.obs import log_text
    assert log_text("ayşe yılmaz") == "'ayşe yılmaz'"
    monkeypatch.setenv("LOG_TEXTS", "false")
    assert log_text("ayşe yılmaz") == "<11 harf>"
