"""proto/ altındaki sözleşmelerden Python kodu üretir → src/sesli_asistan/contracts/.

    python tools/proto/uret.py            # üret
    python tools/proto/uret.py --kontrol  # üretilmiş kod güncel mi (CI): değilse çıkış kodu 1

Üretilen kod repoya girer (kurulumda grpcio-tools gerekmesin diye); CI bu dosyaların proto ile
aynı olduğunu denetler. Üretilen *_grpc.py içindeki mutlak import göreli importa çevrilir.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROTO = ROOT / "proto"
OUT = ROOT / "src" / "sesli_asistan" / "contracts"
HEADER = "# ÜRETİLMİŞ DOSYA — elle değiştirmeyin. Kaynak: proto/{src} · python tools/proto/uret.py\n"


def generate(dest: Path) -> list[Path]:
    from grpc_tools import protoc

    files = sorted(p.relative_to(PROTO).as_posix() for p in PROTO.rglob("*.proto"))
    args = ["protoc", f"-I{PROTO}", f"--python_out={dest}", f"--pyi_out={dest}", f"--grpc_python_out={dest}", *files]
    if protoc.main(args) != 0:
        raise SystemExit("protoc başarısız")
    out = []
    for f in files:
        pkg = dest / Path(f).parent
        for d in [dest, *[dest / Path(*Path(f).parent.parts[:i + 1]) for i in range(len(Path(f).parent.parts))]]:
            (d / "__init__.py").touch()
        stem = Path(f).stem
        for gen in (pkg / f"{stem}_pb2.py", pkg / f"{stem}_pb2.pyi", pkg / f"{stem}_pb2_grpc.py"):
            text = gen.read_text(encoding="utf-8")
            mod = ".".join(Path(f).parent.parts)
            text = re.sub(rf"^from {re.escape(mod)} import {stem}_pb2", f"from . import {stem}_pb2", text, flags=re.M)
            gen.write_text(HEADER.format(src=f) + text, encoding="utf-8")
            out.append(gen)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kontrol", action="store_true")
    a = ap.parse_args(argv)
    if not a.kontrol:
        if OUT.exists():
            shutil.rmtree(OUT)
        OUT.mkdir(parents=True)
        (OUT / "__init__.py").write_text('"""gRPC sözleşmelerinin üretilmiş kodu (proto/). Elle değiştirmeyin."""\n')
        for g in generate(OUT):
            print(g.relative_to(ROOT))
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        stale = []
        for g in generate(tmp):
            cur = OUT / g.relative_to(tmp)
            if not cur.exists() or cur.read_text(encoding="utf-8") != g.read_text(encoding="utf-8"):
                stale.append(str(cur.relative_to(ROOT)))
        if stale:
            print("Üretilmiş kod güncel değil (python tools/proto/uret.py):", *stale, sep="\n  ")
            return 1
    print("üretilmiş kod güncel")
    return 0


if __name__ == "__main__":
    sys.exit(main())
