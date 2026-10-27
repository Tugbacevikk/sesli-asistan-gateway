"""Yedekleme ve geri yükleme (tools/kurulum/yedek.py)."""
import sqlite3
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "kurulum"))
import yedek  # noqa: E402

from sesli_asistan.gateway.adapters.history.sqlite import SqliteCallHistory  # noqa: E402


@pytest.fixture
def ortam(tmp_path, monkeypatch):
    monkeypatch.setattr(yedek, "_gateway_running", lambda: False)
    veri, ayar = tmp_path / "veri", tmp_path / "ayar"
    veri.mkdir()
    ayar.mkdir()
    h = SqliteCallHistory(str(veri / "cagri_gecmisi.db"))
    h.change_add("tugba", "yonetici", "1.2.3.4", "ayarlar", ["ilk"])
    (veri / "yerel_ayarlar.json").write_text('{"aliases": {"c-101": ["Ayşe Abla"]}}', encoding="utf-8")
    (ayar / "gateway.env").write_text("ORGANIZATION_NAME=Deneme\n")
    (ayar / "secrets.env").write_text("API_KEY=gizli\n")
    (ayar / "rehber.json").write_text('{"contacts": []}')
    return veri, ayar, h, tmp_path / "yedekler"


def test_al_dogrula_geri_yukle(ortam):
    veri, ayar, h, hedef = ortam
    arsiv = yedek.take(hedef, veri, ayar)       # Gateway veritabanını açık tutarken (çevrimiçi yedek)
    with tarfile.open(arsiv) as t:
        names = set(t.getnames())
    assert names == {"veri/cagri_gecmisi.db", "veri/yerel_ayarlar.json", "ayar/gateway.env", "ayar/rehber.json",
                     "MANIFEST.json"}                                   # secrets.env varsayılan olarak girmez
    if sys.platform != "win32":
        assert oct(arsiv.stat().st_mode & 0o777) == "0o600"
    # sonradan bozulan / değişen veriler yedekten döner
    h.change_add("kotu", "yonetici", "x", "ayarlar", ["sonra"])
    h.close()
    (veri / "yerel_ayarlar.json").write_text("{bozuk")
    yedek.restore(arsiv, veri, ayar)
    assert (veri / "yerel_ayarlar.json").read_text(encoding="utf-8").startswith('{"aliases"')
    assert (veri / "yerel_ayarlar.json.geri-yukleme-oncesi").read_text() == "{bozuk"
    rows = sqlite3.connect(veri / "cagri_gecmisi.db").execute("SELECT who FROM changes").fetchall()
    assert rows == [("tugba",)]


def test_bozuk_yedek_reddedilir(ortam, tmp_path):
    veri, ayar, h, hedef = ortam
    arsiv = yedek.take(hedef, veri, ayar)
    manifest, files = yedek.read(arsiv)
    files["ayar/gateway.env"] = b"DEGISTI=1\n"
    kotu = tmp_path / "kotu.tar.gz"
    import io
    import json
    with tarfile.open(kotu, "w:gz") as t:
        for n, d in {**files, "MANIFEST.json": json.dumps(manifest).encode()}.items():
            info = tarfile.TarInfo(n)
            info.size = len(d)
            t.addfile(info, io.BytesIO(d))
    with pytest.raises(SystemExit, match="SHA256"):
        yedek.restore(kotu, veri, ayar)
    assert (ayar / "gateway.env").read_text() == "ORGANIZATION_NAME=Deneme\n"   # hiçbir şey yazılmadı


def test_klasor_disina_yazan_arsiv_reddedilir(tmp_path):
    kotu = tmp_path / "k.tar.gz"
    with tarfile.open(kotu, "w:gz") as t:
        info = tarfile.TarInfo("../../etc/passwd")
        info.size = 1
        import io
        t.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(SystemExit, match="geçersiz"):
        yedek.read(kotu)


def test_eski_yedekler_silinir_gizliler_istege_bagli(ortam):
    veri, ayar, h, hedef = ortam
    for i in range(4):
        (hedef / f"sesli-yedek-2026010{i}-000000.tar.gz").parent.mkdir(parents=True, exist_ok=True)
        (hedef / f"sesli-yedek-2026010{i}-000000.tar.gz").write_bytes(b"")
    arsiv = yedek.take(hedef, veri, ayar, tut=2, gizliler=True)
    assert len(list(hedef.glob("sesli-yedek-*.tar.gz"))) == 2 and arsiv.exists()
    assert "ayar/secrets.env" in yedek.read(arsiv)[1]


def test_gateway_calisirken_geri_yuklenmez(ortam, monkeypatch):
    veri, ayar, h, hedef = ortam
    arsiv = yedek.take(hedef, veri, ayar)
    monkeypatch.setattr(yedek, "_gateway_running", lambda: True)
    with pytest.raises(SystemExit, match="çalışıyor"):
        yedek.restore(arsiv, veri, ayar)
