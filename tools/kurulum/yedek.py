"""Yedekleme ve geri yükleme (tasarim.md 14.7): çağrı geçmişi, yerel ayarlar, ayar dosyaları, rehber.

    python yedek.py al --hedef /var/backups/sesli-asistan --tut 14         # günlük (systemd timer)
    python yedek.py listele --hedef /var/backups/sesli-asistan
    python yedek.py dogrula /var/backups/sesli-asistan/sesli-yedek-20261027-031500.tar.gz
    sudo systemctl stop sesli-gateway
    python yedek.py geri-yukle /var/backups/sesli-asistan/sesli-yedek-….tar.gz
    sudo systemctl start sesli-gateway

İçerik: SQLite veritabanı **çevrimiçi yedek API'siyle** alınır (Gateway çalışırken tutarlı kopya; dosya kopyalamak
yazma anında bozuk yedek verebilir), yerel_ayarlar.json, /etc/sesli-asistan/*.env ve rehber.json. `secrets.env`
yalnız `--gizliler` ile girer (yedek dosyası da gizli olur). Her dosyanın SHA256'sı MANIFEST.json'da; geri yüklemede
önce doğrulanır, var olan dosyalar `.geri-yukleme-oncesi` uzantısıyla saklanır. Yedek klasörü 700, dosyalar 600.
Yalnız standart kütüphane.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

VERI = Path("/var/lib/sesli-asistan")
AYAR = Path("/etc/sesli-asistan")
ONEK = "sesli-yedek-"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def collect(veri: Path, ayar: Path, gizliler: bool) -> dict[str, bytes]:
    """Yedeklenecek dosyalar: arşivdeki yol → içerik."""
    out: dict[str, bytes] = {}
    for db in sorted(veri.glob("*.db")):
        with tempfile.TemporaryDirectory() as tmp:
            kopya = Path(tmp) / db.name
            src, dst = sqlite3.connect(f"file:{db}?mode=ro", uri=True), sqlite3.connect(kopya)
            try:
                src.backup(dst)          # çevrimiçi, tutarlı kopya (Gateway yazarken de)
            finally:
                dst.close()
                src.close()
            out[f"veri/{db.name}"] = kopya.read_bytes()
    for f in sorted(veri.glob("*.json")):
        out[f"veri/{f.name}"] = f.read_bytes()
    if ayar.is_dir():
        for f in sorted(ayar.iterdir()):
            if not f.is_file() or f.suffix not in (".env", ".json"):
                continue
            if f.name == "secrets.env" and not gizliler:
                continue
            out[f"ayar/{f.name}"] = f.read_bytes()
    return out


def take(hedef: Path, veri: Path = VERI, ayar: Path = AYAR, tut: int = 14, gizliler: bool = False) -> Path:
    files = collect(veri, ayar, gizliler)
    if not files:
        raise SystemExit(f"yedeklenecek dosya yok ({veri}, {ayar})")
    hedef.mkdir(parents=True, exist_ok=True)
    os.chmod(hedef, 0o700)
    manifest = {"surum": 1, "zaman": time.strftime("%Y-%m-%dT%H:%M:%S"), "gizliler": gizliler,
                "kaynak": {"veri": str(veri), "ayar": str(ayar)},
                "dosyalar": {k: {"sha256": _sha(v), "boyut": len(v)} for k, v in files.items()}}
    ad = hedef / f"{ONEK}{time.strftime('%Y%m%d-%H%M%S')}.tar.gz"
    tmp = ad.with_suffix(".yaziliyor")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    files["MANIFEST.json"] = json.dumps(manifest, ensure_ascii=False, indent=1).encode()
    with os.fdopen(fd, "wb") as raw, tarfile.open(fileobj=raw, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.mode = len(data), int(time.time()), 0o600
            tar.addfile(info, io.BytesIO(data))
    os.replace(tmp, ad)
    verify(ad)
    for old in sorted(hedef.glob(f"{ONEK}*.tar.gz"))[:-tut]:
        old.unlink()
    extra = ", gizliler dahil" if gizliler else ""
    print(f"yedek: {ad} ({ad.stat().st_size // 1024} KB, {len(files) - 1} dosya{extra})")
    return ad


def read(arsiv: Path) -> tuple[dict, dict[str, bytes]]:
    files: dict[str, bytes] = {}
    with tarfile.open(arsiv, "r:gz") as tar:
        for m in tar.getmembers():
            # yalnız düz dosya, göreli ve klasör dışına çıkmayan yollar (zararlı arşiv)
            if not m.isfile() or m.name.startswith("/") or ".." in Path(m.name).parts:
                raise SystemExit(f"arşivde geçersiz öğe: {m.name}")
            files[m.name] = tar.extractfile(m).read()
    if "MANIFEST.json" not in files:
        raise SystemExit("MANIFEST.json yok: sesli asistan yedeği değil")
    return json.loads(files.pop("MANIFEST.json")), files


def verify(arsiv: Path) -> dict:
    manifest, files = read(arsiv)
    want = manifest["dosyalar"]
    if set(want) != set(files):
        raise SystemExit(f"dosya listesi uyuşmuyor: {sorted(set(want) ^ set(files))}")
    bad = [k for k, v in files.items() if _sha(v) != want[k]["sha256"]]
    if bad:
        raise SystemExit(f"SHA256 uyuşmuyor (bozuk yedek): {bad}")
    for name, data in files.items():
        if name.endswith(".db"):
            with tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp) / "k.db"
                p.write_bytes(data)
                con = sqlite3.connect(p)
                ok = con.execute("PRAGMA integrity_check").fetchone()[0]
                con.close()
            if ok != "ok":
                raise SystemExit(f"{name}: SQLite bütünlük denetimi başarısız ({ok})")
    return manifest


def _gateway_running() -> bool:
    if not shutil.which("systemctl"):
        return False
    return subprocess.run(["systemctl", "is-active", "-q", "sesli-gateway"]).returncode == 0


def restore(arsiv: Path, veri: Path = VERI, ayar: Path = AYAR, zorla: bool = False) -> list[Path]:
    manifest = verify(arsiv)
    if _gateway_running() and not zorla:
        raise SystemExit("sesli-gateway çalışıyor: önce 'sudo systemctl stop sesli-gateway' (ya da --zorla)")
    _, files = read(arsiv)
    written = []
    for name, data in files.items():
        kind, fname = name.split("/", 1)
        dest = (veri if kind == "veri" else ayar) / fname
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.copy2(dest, dest.with_name(dest.name + ".geri-yukleme-oncesi"))
        stat = dest.stat() if dest.exists() else None
        tmp = dest.with_name(f".{dest.name}.yeni")
        tmp.write_bytes(data)
        if stat is not None:                         # sahip ve izinler korunur (ör. root:sesli 640)
            os.chmod(tmp, stat.st_mode & 0o777)
            if hasattr(os, "chown") and os.geteuid() == 0:
                os.chown(tmp, stat.st_uid, stat.st_gid)
        os.replace(tmp, dest)
        written.append(dest)
    for f in veri.glob("*.db-journal"):
        f.unlink()
    print(f"geri yüklendi ({manifest['zaman']} yedeği): " + ", ".join(str(p) for p in written))
    print("öncekiler '.geri-yukleme-oncesi' uzantısıyla saklandı. Servisi başlatın: sudo systemctl start sesli-gateway")
    return written


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="komut", required=True)
    a_al = sub.add_parser("al", help="yedek al")
    a_al.add_argument("--hedef", default="/var/backups/sesli-asistan")
    a_al.add_argument("--tut", type=int, default=14, help="saklanacak son yedek sayısı")
    a_al.add_argument("--gizliler", action="store_true", help="secrets.env de girsin")
    a_ls = sub.add_parser("listele")
    a_ls.add_argument("--hedef", default="/var/backups/sesli-asistan")
    a_dg = sub.add_parser("dogrula")
    a_dg.add_argument("arsiv")
    a_gy = sub.add_parser("geri-yukle")
    a_gy.add_argument("arsiv")
    a_gy.add_argument("--zorla", action="store_true", help="Gateway çalışırken de (önerilmez)")
    for p in (a_al, a_gy):
        p.add_argument("--veri", default=str(VERI))
        p.add_argument("--ayar", default=str(AYAR))
    a = ap.parse_args(argv)
    if a.komut == "al":
        take(Path(a.hedef), Path(a.veri), Path(a.ayar), a.tut, a.gizliler)
    elif a.komut == "listele":
        for f in sorted(Path(a.hedef).glob(f"{ONEK}*.tar.gz")):
            print(f"{f.name}  {f.stat().st_size // 1024} KB")
    elif a.komut == "dogrula":
        m = verify(Path(a.arsiv))
        print(f"sağlam: {m['zaman']}, {len(m['dosyalar'])} dosya")
    else:
        restore(Path(a.arsiv), Path(a.veri), Path(a.ayar), a.zorla)
    return 0


if __name__ == "__main__":
    sys.exit(main())
