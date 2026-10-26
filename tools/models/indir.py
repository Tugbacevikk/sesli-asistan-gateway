"""Modelleri models/ klasörüne indirir ve SHA256 ile doğrular.

Kaynak: Vietnamese-speech-to-text-models `scripts/download_models.py` (indirme + MANIFEST.json sha256).
Uyarlanan: katalog kod yerine `deploy/models.yaml`'da; beklenen özetler verilmişse doğrulama
zorunlu (uyuşmazsa hata); mirror desteği; yalnızca Türkçe asistanın modelleri.

    python tools/models/indir.py                     # varsayılan modeller
    python tools/models/indir.py --model faster-whisper-small
    python tools/models/indir.py --dogrula           # indirmeden yalnızca kontrol

İnternetsiz sunucu: internetli makinede indirip models/ klasörünü (MANIFEST.json dahil) taşıyın.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def files_of(out: Path) -> list[Path]:
    return sorted(f for f in ([out] if out.is_file() else out.rglob("*")) if f.is_file()
                  and ".cache" not in f.parts)


def fetch(name: str, spec: dict, dest: Path, mirror: str | None) -> Path:
    out = dest / spec["hedef"]
    if out.exists():
        print(f"var, atlanıyor: {spec['hedef']}")
        return out
    if mirror:
        url_base = f"{mirror.rstrip('/')}/{spec['hedef']}"
        if "hf_repo" in spec:
            out.mkdir(parents=True)
            for fn in spec["dosyalar"]:
                print(f"indiriliyor (mirror): {spec['hedef']}/{fn}")
                urllib.request.urlretrieve(f"{url_base}/{fn}", out / fn)  # nosec B310 (sabit katalog adresi)
        else:
            print(f"indiriliyor (mirror): {spec['hedef']}")
            urllib.request.urlretrieve(url_base, out)  # nosec B310
        return out
    if "hf_repo" in spec:
        from huggingface_hub import snapshot_download

        print(f"indiriliyor: {spec['hf_repo']}")
        snapshot_download(spec["hf_repo"], revision=spec.get("hf_revision"), local_dir=out,
                          allow_patterns=list(spec["dosyalar"]))
        return out
    print(f"indiriliyor: {spec['url']}")
    urllib.request.urlretrieve(spec["url"], out)  # nosec B310 (sabit katalog adresi)
    return out


def verify(name: str, spec: dict, out: Path) -> dict[str, str]:
    """Özetleri hesaplar; katalogda beklenen özet varsa karşılaştırır (uyuşmazsa SystemExit)."""
    got = {f.relative_to(out if out.is_dir() else out.parent).as_posix(): sha256(f) for f in files_of(out)}
    expected = spec.get("sha256") or {}
    if not expected:
        print(f"UYARI {name}: katalogda beklenen sha256 yok (doğrulanmamış); MANIFEST.json'a yazıldı")
        return got
    bad = [k for k, v in expected.items() if got.get(k) != v]
    if bad:
        raise SystemExit(f"HATA {name}: sha256 uyuşmuyor ya da dosya eksik: {bad}")
    print(f"doğrulandı: {name}")
    return got


def main(argv=None) -> int:
    import yaml

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--katalog", default=str(ROOT / "deploy" / "models.yaml"))
    ap.add_argument("--hedef", default=str(ROOT / "models"))
    ap.add_argument("--model", action="append",
                    help="yalnızca bu model(ler); varsayılan: katalogda varsayilan olanlar")
    ap.add_argument("--dogrula", action="store_true", help="indirmeden yalnızca doğrula")
    a = ap.parse_args(argv)

    cat = yaml.safe_load(Path(a.katalog).read_text(encoding="utf-8"))
    models = cat["models"]
    names = a.model or [k for k, v in models.items() if v.get("varsayilan")]
    unknown = [n for n in names if n not in models]
    if unknown:
        raise SystemExit(f"katalogda yok: {unknown} (seçenekler: {sorted(models)})")

    dest = Path(a.hedef)
    dest.mkdir(parents=True, exist_ok=True)
    manifest_path = dest / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    for n in names:
        spec = models[n]
        out = dest / spec["hedef"]
        if not a.dogrula:
            out = fetch(n, spec, dest, cat.get("mirror"))
        elif not out.exists():
            raise SystemExit(f"HATA {n}: {out} yok")
        manifest[n] = {"lisans": spec["lisans"], "kaynak": spec.get("hf_repo") or spec.get("url"),
                       "revizyon": spec.get("hf_revision"), "sha256": verify(n, spec, out)}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"MANIFEST: {manifest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
