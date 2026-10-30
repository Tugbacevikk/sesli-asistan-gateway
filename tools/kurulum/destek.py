"""Destek paketi (tasarim.md 14.8): sorun bildirirken tek dosya — sürüm, sistem, servis durumu, sağlık, loglar, ayarlar.

    sudo python3 /opt/sesli-asistan/current/tools/kurulum/destek.py               # → /tmp/sesli-destek-<zaman>.tar.gz
    sudo python3 …/destek.py --saat 48 --cikti /root                             # son 48 saatin logları

Gizlilik — pakete GİRMEZ:
  - secrets.env içeriği (yalnız hangi anahtarların dolu olduğu), ayar dosyalarındaki anahtar / şifre değerleri,
  - loglarda Bearer / anahtar= / password değerleri (maskelenir), arayan numaraları (son 4 hane dışında),
  - rehberdeki adlar (yalnız kayıt sayısı), çağrı geçmişinin metinleri (yalnız sonuç sayıları), ses.
  Loglarda arayanın söyledikleri ancak LOG_TEXTS=true ise vardır (üretimde false); OZET.txt bunu ayrıca belirtir.
  Paketi göndermeden önce açıp bakabilirsiniz: tar -tzf <paket> / tar -xzf <paket>.
Yalnız standart kütüphane: Gateway çalışmıyorken de (venv bozuk olsa da) sistem python3'üyle çalışır.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import tarfile
import time
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path

ONEK = Path("/opt/sesli-asistan")
AYAR = Path("/etc/sesli-asistan")
VERI = Path("/var/lib/sesli-asistan")
SERVISLER = ("sesli-gateway", "sesli-stt", "sesli-tts", "sesli-yedek.timer", "ollama")
GIZLI_ANAHTAR = re.compile(r"(KEY|SIFRE|PASSWORD|PASS|TOKEN|SECRET|USERS)", re.I)

# Log ve komut çıktılarında maskelenenler
_MASKE = [
    (re.compile(r"(?i)(authorization:\s*bearer\s+)\S+"), r"\1***"),
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"), r"\1***"),
    (re.compile(r"(?i)((?:anahtar|api_key|apikey|key|token|password|sifre|şifre|secret)[\"']?\s*[=:]\s*[\"']?)"
                r"(?!\*\*\*|\(boş\))[^\s\"'&,}]+"), r"\1***"),
    # telefon numaraları: 7+ haneli diziler son 4 hane dışında (dahili numaralar 2–6 hane: dokunulmaz)
    (re.compile(r"(?<![\d.])(\+?\d{3,})(\d{4})(?![\d.])"), lambda m: "*" * len(m.group(1)) + m.group(2)),
]


def mask(text: str, secrets: Sequence[str] = ()) -> str:
    for s in secrets:
        if len(s) >= 6:
            text = text.replace(s, "***")
    for rx, repl in _MASKE:
        text = rx.sub(repl, text)
    return text


Runner = Callable[[list[str], float], str]


def run(cmd: list[str], timeout: float = 20) -> str:
    if not shutil.which(cmd[0]):
        return f"({cmd[0]} yok)\n"
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, errors="replace")
        return r.stdout + (f"\n[stderr]\n{r.stderr}" if r.stderr.strip() else "")
    except subprocess.TimeoutExpired:
        return f"(zaman aşımı: {' '.join(cmd)})\n"
    except OSError as e:
        return f"({e})\n"


def env_masked(path: Path) -> str:
    """Ayar dosyası: anahtar / şifre içeren değişkenlerin değeri '***' (doluysa) ya da '(boş)'."""
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$", line)
        if m and GIZLI_ANAHTAR.search(m.group(1)):
            out.append(f"{m.group(1)}={'***' if m.group(2).strip() else '(boş)'}")
        else:
            out.append(line)
    return "\n".join(out) + "\n"


def secret_values(ayar: Path) -> list[str]:
    """Loglarda ve çıktılarda aranıp silinecek gerçek gizli değerler (UI_USERS'taki kişisel anahtarlar dahil)."""
    vals: list[str] = []
    for f in ayar.glob("*.env"):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for k, v in re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$", text, re.M):
            v = v.strip().strip("'\"")
            if GIZLI_ANAHTAR.search(k) and v:
                vals.append(v)
                if k.upper() == "UI_USERS":
                    vals += [p.split(":", 2)[2].strip() for p in v.split(",") if p.count(":") >= 2]
    return sorted(set(vals), key=len, reverse=True)


def api_key(ayar: Path) -> str:
    try:
        m = re.search(r"^API_KEY=(.*)$", (ayar / "secrets.env").read_text(encoding="utf-8"), re.M)
        return m.group(1).strip() if m else ""
    except OSError:
        return ""


def http_get(url: str, key: str, timeout: float = 5) -> str:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"} if key else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310 (yalnız yerel adres)
            return r.read(2_000_000).decode("utf-8", errors="replace")
    except Exception as e:
        return f"(ulaşılamadı: {e})\n"


def directory_summary(path: Path) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cs = [c for c in data.get("contacts", []) if not c.get("deleted")]
        return json.dumps({"dosya": str(path), "kayit": len(cs),
                           "kisi": sum(1 for c in cs if (c.get("kind") or "person") == "person"),
                           "birim_hatti": sum(1 for c in cs if c.get("kind") == "department"),
                           "aktarilamaz": sum(1 for c in cs if c.get("transferable") is False),
                           "server_time": data.get("server_time"), "kaynak": data.get("_kaynak", "")},
                          ensure_ascii=False, indent=1)
    except (OSError, ValueError) as e:
        return f"(okunamadı: {e})\n"


def history_summary(db: Path) -> str:
    """Son 7 günün çağrı sonuçları ve şema sürümü — metin yok."""
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            since = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 7 * 86400))
            out = {"sema_surumu": con.execute("PRAGMA user_version").fetchone()[0],
                   "son_7_gun": dict(con.execute("SELECT COALESCE(outcome,'?'), COUNT(*) FROM calls WHERE started >= ? "
                                                 "GROUP BY outcome", (since,)).fetchall()),
                   "toplam_kayit": con.execute("SELECT COUNT(*) FROM calls").fetchone()[0],
                   "butunluk": con.execute("PRAGMA quick_check").fetchone()[0]}
        finally:
            con.close()
        return json.dumps(out, ensure_ascii=False, indent=1)
    except sqlite3.Error as e:
        return f"(okunamadı: {e})\n"


def env_value(ayar: Path, key: str, default: str = "") -> str:
    try:
        m = re.findall(rf"^{key}=(.*)$", (ayar / "gateway.env").read_text(encoding="utf-8"), re.M)
        return m[-1].strip() if m else default
    except OSError:
        return default


def collect(ayar: Path = AYAR, veri: Path = VERI, onek: Path = ONEK, saat: int = 24,
            gateway: str = "http://127.0.0.1:8000", runner: Runner = run) -> dict[str, str]:
    files: dict[str, str] = {}
    secrets = secret_values(ayar)
    key = api_key(ayar)
    cur = onek / "current"
    files["surum.txt"] = "\n".join([
        f"etkin: {os.path.realpath(cur) if cur.exists() else '(kurulu değil)'}",
        f"SURUM: {(cur / 'SURUM').read_text().strip() if (cur / 'SURUM').exists() else '-'}",
        "kurulu sürümler: " + ", ".join(sorted(p.name for p in (onek / "releases").glob("*"))
                                        if (onek / "releases").exists() else []),
        f"zaman: {time.strftime('%Y-%m-%dT%H:%M:%S%z')}", ""])
    files["sistem.txt"] = "".join([
        f"{platform.platform()}\n{platform.python_version()}\n\n",
        Path("/etc/os-release").read_text() if Path("/etc/os-release").exists() else "",
        "\n## uptime\n", runner(["uptime"], 5),
        "\n## bellek\n", runner(["free", "-m"], 5),
        "\n## disk\n", runner(["df", "-h", "/", str(onek.parent), str(veri.parent), "/var/log"], 5),
        "\n## GPU\n", runner(["nvidia-smi"], 15),
        "\n## dinlenen portlar\n", runner(["ss", "-ltnup"], 5),
        "\n## saat eşitleme\n", runner(["timedatectl"], 5)])
    files["servisler.txt"] = "".join(
        f"\n## {s}\n" + runner(["systemctl", "status", "--no-pager", "-n", "0", s], 10)
        + runner(["systemctl", "show", s, "-p", "NRestarts,ActiveEnterTimestamp,ExecMainStatus,Result"], 5)
        for s in SERVISLER)
    for s in SERVISLER[:3] + ("ollama",):
        files[f"loglar/{s}.log"] = runner(["journalctl", "-u", s, "--since", f"-{saat}h", "--no-pager",
                                           "-o", "short-iso", "-n", "20000"], 60)
    files["saglik.json"] = http_get(f"{gateway}/v1/admin/health", key)
    files["metrikler.txt"] = http_get(f"{gateway}/metrics", key)
    if ayar.is_dir():
        for f in sorted(ayar.glob("*.env")):
            files[f"ayar/{f.name}"] = (env_masked(f) if f.name != "secrets.env" else
                                       "# içerik pakete girmez; yalnız doluluk:\n" + env_masked(f))
    files["rehber.json"] = directory_summary(Path(env_value(ayar, "DIRECTORY_PATH", str(veri / "rehber.json"))))
    files["cagri_gecmisi.json"] = history_summary(Path(env_value(ayar, "CALL_HISTORY_DB",
                                                                 str(veri / "cagri_gecmisi.db"))))
    texts = env_value(ayar, "LOG_TEXTS", "true").lower()
    files["OZET.txt"] = (
        "Sesli asistan destek paketi\n"
        f"Oluşturma: {time.strftime('%Y-%m-%d %H:%M:%S')} · log aralığı: son {saat} saat\n"
        f"Loglarda konuşma metinleri: {'VAR (LOG_TEXTS=true)' if texts in ('1', 'true', 'yes') else 'yok'}\n"
        "Maskelenenler: anahtar/şifre değerleri, Bearer, 7+ haneli numaralar (son 4 hane hariç).\n"
        "Girmeyenler: secrets.env değerleri, rehberdeki adlar, çağrı metinleri, ses.\n")
    return {k: mask(v, secrets) for k, v in files.items()}


def write(files: dict[str, str], cikti: Path) -> Path:
    cikti.mkdir(parents=True, exist_ok=True)
    ad = cikti / f"sesli-destek-{time.strftime('%Y%m%d-%H%M%S')}.tar.gz"
    fd = os.open(ad, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as raw, tarfile.open(fileobj=raw, mode="w:gz") as tar:
        for name, text in files.items():
            data = text.encode("utf-8")
            info = tarfile.TarInfo(f"sesli-destek/{name}")
            info.size, info.mtime, info.mode = len(data), int(time.time()), 0o600
            tar.addfile(info, io.BytesIO(data))
    return ad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Sesli asistan destek paketi")
    ap.add_argument("--cikti", default="/tmp")
    ap.add_argument("--saat", type=int, default=24, help="logların kaç saati")
    ap.add_argument("--ayar", default=str(AYAR))
    ap.add_argument("--veri", default=str(VERI))
    ap.add_argument("--onek", default=str(ONEK))
    ap.add_argument("--gateway", default="http://127.0.0.1:8000")
    a = ap.parse_args(argv)
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        print("UYARI: root değil: ayar dosyaları ve loglar okunamayabilir (sudo ile çalıştırın)")
    files = collect(Path(a.ayar), Path(a.veri), Path(a.onek), a.saat, a.gateway)
    ad = write(files, Path(a.cikti))
    print(f"destek paketi: {ad} ({ad.stat().st_size // 1024} KB)")
    print("İçeriğe göndermeden önce bakabilirsiniz: tar -tzf " + str(ad))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
