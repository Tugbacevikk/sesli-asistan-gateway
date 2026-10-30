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


def _kaldir_ortami(tmp_path):
    """Geçici klasörlerde sahte kurulum; systemctl / userdel sahte (gerçek sistem değişmez)."""
    import os
    import shutil
    import sys

    onek = tmp_path / "opt" / "sesli-asistan"
    rel = onek / "releases" / "0.1.0"
    (rel / "venv" / "gateway" / "bin").mkdir(parents=True)
    os.symlink(sys.executable, rel / "venv" / "gateway" / "bin" / "python")
    (rel / "tools" / "kurulum").mkdir(parents=True)
    shutil.copy(ROOT / "tools/kurulum/yedek.py", rel / "tools/kurulum/yedek.py")
    os.symlink(rel, onek / "current")
    (onek / "models").mkdir()
    ayar, veri, yedek, onb = (tmp_path / n for n in ("etc", "lib", "yedek", "cache"))
    for d in (ayar, veri, onb):
        d.mkdir()
    (ayar / "gateway.env").write_text("DIRECTORY_SOURCE=json\n")
    (ayar / "secrets.env").write_text("API_KEY=gizli\n")
    (veri / "rehber.json").write_text('{"contacts": []}')
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for cmd in ("systemctl", "userdel"):
        f = bin_ / cmd
        f.write_text(f'#!/bin/sh\necho "{cmd} $*" >> "{tmp_path}/komutlar.log"\n')
        f.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "SESLI_AYAR": str(ayar), "SESLI_VERI": str(veri),
           "SESLI_YEDEK": str(yedek), "SESLI_ONBELLEK": str(onb)}
    return onek, ayar, veri, yedek, onb, env


def _root_degilse_atla():
    import os

    if os.name != "posix" or os.geteuid() != 0 or Path("/etc/systemd/system/sesli-gateway.service").exists():
        pytest.skip("kaldırma denemesi root ister ve gerçek kurulum olmayan makinede çalışır")


def test_kaldir_veriler_kalir(tmp_path):
    _root_degilse_atla()
    onek, ayar, veri, yedek, onb, env = _kaldir_ortami(tmp_path)
    r = subprocess.run(["bash", "deploy/install.sh", "--kaldir", "--evet", "--onek", str(onek)], cwd=ROOT, env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not onek.exists() and not onb.exists()
    assert (ayar / "secrets.env").exists() and (veri / "rehber.json").exists() and not yedek.exists()
    log = (tmp_path / "komutlar.log").read_text()
    assert "disable -q --now sesli-gateway" in log and "daemon-reload" in log and "userdel" not in log


def test_kaldir_verilerle_once_yedek_alir(tmp_path):
    _root_degilse_atla()
    onek, ayar, veri, yedek, onb, env = _kaldir_ortami(tmp_path)
    r = subprocess.run(["bash", "deploy/install.sh", "--kaldir", "--verilerle", "--evet", "--onek", str(onek)],
                       cwd=ROOT, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not onek.exists() and not ayar.exists() and not veri.exists()
    arsiv = list(yedek.glob("sesli-yedek-*.tar.gz"))
    assert len(arsiv) == 1                                      # son yedek (gizliler dahil) duruyor
    import tarfile
    with tarfile.open(arsiv[0]) as t:
        assert {"ayar/secrets.env", "veri/rehber.json"} <= set(t.getnames())
    has_user = subprocess.run(["id", "sesli"], capture_output=True).returncode == 0
    assert ("userdel sesli" in (tmp_path / "komutlar.log").read_text()) == has_user


def test_kaldir_onaysiz_ve_tehlikeli_onek_reddedilir(tmp_path):
    _root_degilse_atla()
    onek, ayar, veri, yedek, onb, env = _kaldir_ortami(tmp_path)
    r = subprocess.run(["bash", "deploy/install.sh", "--kaldir", "--onek", str(onek)], cwd=ROOT, env=env,
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert r.returncode != 0 and "--evet" in r.stdout and onek.exists()
    r = subprocess.run(["bash", "deploy/install.sh", "--kaldir", "--evet", "--onek", "/usr"], cwd=ROOT, env=env,
                       capture_output=True, text=True)
    assert r.returncode != 0 and "beklenmedik" in r.stdout


# --- paket imzası (tasarim.md 14.3) ---------------------------------------------------------------------------

def _gpg_anahtari(home, ad):
    home.mkdir(mode=0o700)
    env = {**os.environ, "GNUPGHOME": str(home)}
    subprocess.run(["gpg", "--batch", "--passphrase", "", "--quick-gen-key", f"{ad} <{ad}@ornek.invalid>",
                    "ed25519", "sign", "1d"], env=env, check=True, capture_output=True)
    pub = subprocess.run(["gpg", "--batch", "--export", "--armor"], env=env, check=True, capture_output=True).stdout
    return env, pub


def _imza_dogrula(tmp_path, paket, imzasiz=0, anahtar=""):
    """install.sh'taki imza_dogrula işlevi tek başına (kurulumun geri kalanı çalışmadan)."""
    fn = re.search(r"^IMZA_HALKASI=.*?^}\n", (ROOT / "deploy/install.sh").read_text(), re.S | re.M).group(0)
    script = ('set -Eeuo pipefail\nbilgi() { echo "BILGI $*"; }\nuyari() { echo "UYARI $*"; }\n'
              'dur() { echo "DUR $*"; exit 1; }\n' + fn + "imza_dogrula\n")
    env = {**os.environ, "AYAR": str(tmp_path / "ayar"), "PAKET": str(paket), "IMZASIZ": str(imzasiz),
           "IMZA_ANAHTARI": str(anahtar), "GNUPGHOME": str(tmp_path / "bos-gpg")}
    (tmp_path / "bos-gpg").mkdir(mode=0o700, exist_ok=True)
    return subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)


@pytest.mark.skipif(not shutil.which("gpg") or not shutil.which("gpgv") or os.name != "posix", reason="gpg yok")
def test_paket_imzasi_dogrulanmadan_kurulmaz(tmp_path):
    yayinci, pub = _gpg_anahtari(tmp_path / "yayinci", "yayinci")
    saldirgan, _ = _gpg_anahtari(tmp_path / "saldirgan", "saldirgan")
    (tmp_path / "pub.asc").write_bytes(pub)
    paket = tmp_path / "sesli-asistan-0.2.0.tar.gz"
    paket.write_bytes(b"paket icerigi")
    sig = Path(f"{paket}.sig")

    def imzala(env):
        subprocess.run(["gpg", "--batch", "--yes", "--detach-sign", "-o", str(sig), str(paket)], env=env, check=True,
                       capture_output=True)

    r = _imza_dogrula(tmp_path, paket)                               # anahtar tanımlı değil
    assert r.returncode != 0 and "--imza-anahtari" in r.stdout
    assert _imza_dogrula(tmp_path, paket, imzasiz=1).returncode == 0
    imzala(yayinci)
    r = _imza_dogrula(tmp_path, paket, anahtar=tmp_path / "pub.asc")   # ilk kurulum: anahtar konur + doğrulanır
    assert r.returncode == 0 and "imzası doğrulandı" in r.stdout, r.stdout + r.stderr
    assert (tmp_path / "ayar" / "imza-anahtari.gpg").exists()
    assert _imza_dogrula(tmp_path, paket).returncode == 0            # sonraki güncelleme: kayıtlı anahtarla
    paket.write_bytes(b"paket icerigi + arka kapi")                  # değiştirilmiş paket
    r = _imza_dogrula(tmp_path, paket)
    assert r.returncode != 0 and "DOĞRULANAMADI" in r.stdout
    imzala(saldirgan)                                                # başka anahtarla imzalanmış
    assert _imza_dogrula(tmp_path, paket).returncode != 0
    assert _imza_dogrula(tmp_path, paket, imzasiz=1).returncode != 0  # anahtar varken --imzasiz imzayı atlatamaz
    sig.unlink()
    r = _imza_dogrula(tmp_path, paket)
    assert r.returncode != 0 and "imza dosyası yok" in r.stdout
