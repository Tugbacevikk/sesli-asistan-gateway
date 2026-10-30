"""Kurulum dosyaları (deploy/): ayar şablonları geçerli, kilitler pyproject ile uyumlu, systemd birimleri tutarlı."""
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"


def env_file(tmp_path, name):
    p = tmp_path / name
    p.write_text((DEPLOY / "ayar" / name).read_text(encoding="utf-8").replace("@ONEK@", "/opt/sesli-asistan"),
                 encoding="utf-8")
    return str(p)


def test_ayar_sablonlari_gecerli(tmp_path, monkeypatch):
    from sesli_asistan.gateway.config import GatewaySettings
    from sesli_asistan.stt.config import SttSettings
    from sesli_asistan.tts.config import TtsSettings

    for k in ("CALL_HISTORY_DB", "LOCAL_SETTINGS_FILE"):
        monkeypatch.delenv(k, raising=False)
    g = GatewaySettings(_env_file=(env_file(tmp_path, "gateway.env"), env_file(tmp_path, "secrets.env")))
    assert g.local_settings_file.startswith("/var/lib/sesli-asistan/") and g.call_history_db.startswith("/var/lib/")
    assert not g.office().always_open and g.ui_ips == {"127.0.0.1", "::1"}     # tatiller tanımlı, arayüz yerel
    s = SttSettings(_env_file=env_file(tmp_path, "stt.env"))
    assert s.model_dir == "/opt/sesli-asistan/models/faster-whisper-medium" and s.debug_dir == ""   # ses kaydı kapalı
    assert TtsSettings(_env_file=env_file(tmp_path, "tts.env")).voice == "ayse"


def test_gizli_degerler_yalniz_secrets_env_de():
    for name in ("gateway.env", "stt.env", "tts.env"):
        text = (DEPLOY / "ayar" / name).read_text(encoding="utf-8")
        assert not re.search(r"^(API_KEY|PBX_API_KEY)=", text, re.M), name
    assert re.search(r"^API_KEY=$", (DEPLOY / "ayar" / "secrets.env").read_text(), re.M)   # kurulum üretir


def _deps(spec: list[str]) -> set[str]:
    return {re.split(r"[<>=!;\[ ]", d, maxsplit=1)[0].lower().replace("_", "-") for d in spec
            if "win32" not in d}         # yalnız Windows'ta gereken (tzdata): Linux kilidinde yok


@pytest.mark.parametrize("lock,extras", [("gateway", ["gateway"]), ("stt", ["stt"]), ("stt-gpu", ["stt", "stt-cuda"]),
                                         ("tts", ["tts"]), ("araclar", ["tools"])])
def test_kilitler_pyproject_ile_uyumlu(lock, extras):
    proj = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    want = _deps(proj["dependencies"]) | set().union(*(_deps(proj["optional-dependencies"][e]) for e in extras))
    text = (DEPLOY / "kilit" / f"{lock}.txt").read_text(encoding="utf-8")
    pinned = {m.group(1).lower() for m in re.finditer(r"^([A-Za-z0-9_.\-]+)==", text, re.M)}
    assert want <= pinned, f"kilitte yok: {want - pinned} → tools/kurulum/kilit.sh"
    for block in re.split(r"\n(?=[A-Za-z0-9])", text.strip()):
        assert "--hash=sha256:" in block, f"özetsiz paket: {block.splitlines()[0]}"


def test_systemd_birimleri():
    for svc, mod in (("gateway", "sesli_asistan.gateway"), ("stt", "sesli_asistan.stt"), ("tts", "sesli_asistan.tts")):
        text = (DEPLOY / "systemd" / f"sesli-{svc}.service").read_text(encoding="utf-8")
        assert f"ExecStart=@ONEK@/current/venv/{svc}/bin/python -m {mod}" in text
        assert "User=sesli" in text and "NoNewPrivileges=yes" in text and "ProtectSystem=strict" in text
        assert f"EnvironmentFile=/etc/sesli-asistan/{svc}.env" in text
        assert (ROOT / "src" / Path(*mod.split(".")) / "__main__.py").exists()
    gw = (DEPLOY / "systemd" / "sesli-gateway.service").read_text(encoding="utf-8")
    assert "EnvironmentFile=/etc/sesli-asistan/secrets.env" in gw


def test_lisans_listesi_kilitlerle_guncel():
    r = subprocess.run([sys.executable, str(ROOT / "tools/kurulum/lisanslar.py"), "--kontrol"], capture_output=True,
                       text=True)
    assert r.returncode == 0, r.stdout


@pytest.mark.skipif(os.name == "nt" or shutil.which("shellcheck") is None, reason="shellcheck yok / Windows")
def test_betikler_shellcheck():
    r = subprocess.run(["shellcheck", "-S", "warning", "deploy/install.sh", "deploy/paket.sh",
                        "tools/kurulum/kilit.sh", "deploy/asterisk/kur.sh"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


# Windows'ta "bash" çoğunlukla WSL yönlendiricisidir (C:\ yollarını tanımaz): betikler Linux içindir, orada denenir
@pytest.mark.skipif(os.name == "nt" or shutil.which("bash") is None, reason="Linux betikleri; bash yok / Windows")
def test_betikler_bash_sozdizimi():
    for f in ("deploy/install.sh", "deploy/paket.sh", "tools/kurulum/kilit.sh", "deploy/asterisk/kur.sh"):
        assert subprocess.run(["bash", "-n", f], cwd=ROOT).returncode == 0, f


def test_bosalt_araci_gateway_kapaliysa_hata_vermez():
    import sys as _s
    _s.path.insert(0, str(ROOT / "tools" / "kurulum"))
    import bosalt
    assert bosalt.main(["--port", "1", "--bekle", "0"]) == 0
