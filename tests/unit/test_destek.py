"""Destek paketi (tasarim.md 14.8): içerik ve gizlilik."""
import json
import sqlite3
import sys
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "kurulum"))
import destek  # noqa: E402

API_KEY = "cok-gizli-api-anahtari-123"
UI_KEY = "ayse-kisisel-anahtar-4567"


def kurulum(tmp_path):
    ayar, veri, onek = tmp_path / "etc", tmp_path / "lib", tmp_path / "opt"
    ayar.mkdir()
    veri.mkdir()
    (onek / "releases" / "0.2.0-abc").mkdir(parents=True)
    (onek / "releases" / "0.2.0-abc" / "SURUM").write_text("0.2.0-abc\n")
    (onek / "current").symlink_to(onek / "releases" / "0.2.0-abc")
    (ayar / "gateway.env").write_text(
        f"ORGANIZATION_NAME=Deneme\nPBX_BASE_URL=http://10.0.0.5:8100\nDIRECTORY_PATH={veri}/rehber.json\n"
        f"CALL_HISTORY_DB={veri}/g.db\nLOG_TEXTS=false\nPBX_API_KEY=santral-anahtari-999\n")
    (ayar / "secrets.env").write_text(f"API_KEY={API_KEY}\nPBX_API_KEY=\nUI_USERS=Ayşe:yonetici:{UI_KEY}\n")
    (veri / "rehber.json").write_text(json.dumps({"contacts": [
        {"id": "1", "first_name": "Gizli", "last_name": "Kişi", "extension": "214"},
        {"id": "2", "kind": "department", "department": "Muhasebe", "extension": "210"}]}))
    from sesli_asistan.gateway.adapters.history.sqlite import SqliteCallHistory
    from sesli_asistan.gateway.core.call import CallStats, TurnRecord

    h = SqliteCallHistory(str(veri / "g.db"))
    h.add("c1", CallStats(turns=[TurnRecord("ayşe yılmazı istiyorum", "aktarıyorum", ["transfer:214"], 0.1, 1)],
                          outcome="transferred", started="2099-01-01T00:00:00"))
    h.close()
    return ayar, veri, onek


def sahte_komut(cmd, timeout):
    if cmd[0] == "journalctl":
        return (f"Eki 30 sesli-gateway: istek Authorization: Bearer {API_KEY} geldi\n"
                f"arayan 05321234567 ui?anahtar={UI_KEY}\n"
                f"çağrı aktarıldı dahili 214, süre 12.5 sn\n")
    return f"{' '.join(cmd)} çıktısı\n"


class _Saglik(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = json.dumps({"status": "ok", "auth": self.headers.get("Authorization", "")}).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def test_paket_icerigi_ve_gizlilik(tmp_path):
    ayar, veri, onek = kurulum(tmp_path)
    srv = HTTPServer(("127.0.0.1", 0), _Saglik)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        files = destek.collect(ayar, veri, onek, 24, f"http://127.0.0.1:{srv.server_port}", runner=sahte_komut)
    finally:
        srv.shutdown()
    ad = destek.write(files, tmp_path / "cikti")
    assert oct(ad.stat().st_mode & 0o777) == "0o600"
    with tarfile.open(ad) as t:
        names = set(t.getnames())
        allt = "\n".join(t.extractfile(m).read().decode() for m in t.getmembers())
    assert {"sesli-destek/OZET.txt", "sesli-destek/surum.txt", "sesli-destek/saglik.json",
            "sesli-destek/loglar/sesli-gateway.log", "sesli-destek/ayar/gateway.env",
            "sesli-destek/rehber.json", "sesli-destek/cagri_gecmisi.json"} <= names
    # gizli değerler hiçbir yerde yok
    for secret in (API_KEY, UI_KEY, "santral-anahtari-999", "05321234567", "Gizli", "ayşe yılmazı"):
        assert secret not in allt, secret
    assert "*******4567" in allt                              # numara: son 4 hane kalır
    assert "dahili 214" in allt                                # dahili numaralar maskelenmez
    assert "0.2.0-abc" in files["surum.txt"]
    assert '"status": "ok"' in files["saglik.json"] and "Bearer ***" in files["saglik.json"]
    gw = files["ayar/gateway.env"]
    assert "PBX_API_KEY=***" in gw and "PBX_BASE_URL=http://10.0.0.5:8100" in gw
    assert "API_KEY=***" in files["ayar/secrets.env"] and "PBX_API_KEY=(boş)" in files["ayar/secrets.env"]
    r = json.loads(files["rehber.json"])
    assert (r["kayit"], r["kisi"], r["birim_hatti"]) == (2, 1, 1)
    c = json.loads(files["cagri_gecmisi.json"])
    assert c["son_7_gun"] == {"transferred": 1} and c["butunluk"] == "ok" and c["sema_surumu"] >= 2
    assert "Loglarda konuşma metinleri: yok" in files["OZET.txt"]


def test_gateway_kapali_ve_dosyalar_yokken_de_paket_olusur(tmp_path):
    files = destek.collect(tmp_path / "yok", tmp_path / "yok", tmp_path / "yok", 1, "http://127.0.0.1:1",
                           runner=sahte_komut)
    assert "ulaşılamadı" in files["saglik.json"] and "okunamadı" in files["rehber.json"]
    assert "(kurulu değil)" in files["surum.txt"]
    assert destek.write(files, tmp_path).exists()


def test_maske():
    m = destek.mask
    assert m('{"api_key": "abc123xyz"}') == '{"api_key": "***"}'
    assert m("GET /ui?anahtar=abcdef123&x=1") == "GET /ui?anahtar=***&x=1"
    assert m("2026-10-30T10:45:12 port 10000 dahili 214") == "2026-10-30T10:45:12 port 10000 dahili 214"
    assert m("+905321234567") == "*********4567"
    assert m("şifre: abc", ["abcdef"]) == "şifre: ***"


def test_db_salt_okunur_acilir(tmp_path):
    db = tmp_path / "g.db"
    sqlite3.connect(db).close()
    assert "okunamadı" in destek.history_summary(db)            # tablo yok: hata metni, istisna değil
