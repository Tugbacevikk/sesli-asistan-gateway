"""Sürüm yönetimi (tasarim.md 14.9): CHANGELOG.md ile pyproject.toml sürümünün tutarlılığı ve sürüm hazırlama.

    python tools/kurulum/surum.py hazirla 0.2.0       # "Yayımlanmamış" → "0.2.0 — bugün", pyproject sürümü 0.2.0
    python tools/kurulum/surum.py denetle v0.2.0      # CI: etiket = pyproject sürümü ve CHANGELOG'da bölümü var
    python tools/kurulum/surum.py notlar 0.2.0        # o sürümün notları (GitHub sürüm açıklaması)

Yalnız standart kütüphane.
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

KOK = Path(__file__).resolve().parents[2]
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
YAYIMLANMAMIS = "Yayımlanmamış"
REPO = "https://github.com/Tugbacevikk/sesli-asistan-gateway"


def pyproject_version(kok: Path = KOK) -> str:
    m = re.search(r'^version\s*=\s*"([^"]+)"', (kok / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    if not m:
        raise SystemExit("pyproject.toml'da version yok")
    return m.group(1)


def sections(text: str) -> dict[str, str]:
    """'## [x]' başlıkları → bölüm metni (başlık hariç, bağlantı tanımları hariç)."""
    out: dict[str, str] = {}
    parts = re.split(r"^## \[([^\]]+)\][^\n]*\n", text, flags=re.M)
    for name, body in zip(parts[1::2], parts[2::2], strict=True):
        body = re.sub(r"^\[[^\]]+\]: \S+\s*$", "", body, flags=re.M)
        out[name] = body.strip()
    return out


def notes(version: str, kok: Path = KOK) -> str:
    sec = sections((kok / "CHANGELOG.md").read_text(encoding="utf-8"))
    if version not in sec:
        raise SystemExit(f"CHANGELOG.md'de [{version}] bölümü yok")
    return sec[version]


def check_tag(tag: str, kok: Path = KOK) -> str:
    version = tag.removeprefix("v")
    if not SEMVER.match(version):
        raise SystemExit(f"etiket anlamsal sürüm değil: {tag} (ör. v0.2.0)")
    if version != (pv := pyproject_version(kok)):
        raise SystemExit(f"etiket {tag}, pyproject.toml sürümü {pv}: önce 'surum.py hazirla {version}'")
    if not notes(version, kok).strip():
        raise SystemExit(f"CHANGELOG.md [{version}] bölümü boş")
    return version


def prepare(version: str, kok: Path = KOK, today: dt.date | None = None) -> None:
    if not SEMVER.match(version):
        raise SystemExit(f"anlamsal sürüm değil: {version} (ör. 0.2.0)")
    old = pyproject_version(kok)
    if tuple(map(int, version.split("."))) <= tuple(map(int, old.split("."))):
        raise SystemExit(f"yeni sürüm ({version}) mevcut sürümden ({old}) büyük olmalı")
    path = kok / "CHANGELOG.md"
    text = path.read_text(encoding="utf-8")
    sec = sections(text)
    if version in sec:
        raise SystemExit(f"CHANGELOG.md'de [{version}] zaten var")
    if not sec.get(YAYIMLANMAMIS, "").strip():
        raise SystemExit("CHANGELOG.md 'Yayımlanmamış' bölümü boş: sürüme girecek değişiklik yok")
    date = (today or dt.date.today()).isoformat()
    text = text.replace(f"## [{YAYIMLANMAMIS}]\n", f"## [{YAYIMLANMAMIS}]\n\n## [{version}] — {date}\n", 1)
    links = (f"[{YAYIMLANMAMIS}]: {REPO}/compare/v{version}...HEAD\n"
             f"[{version}]: {REPO}/compare/v{old}...v{version}")
    text = re.sub(rf"^\[{YAYIMLANMAMIS}\]: .*$", links, text, count=1, flags=re.M)
    path.write_text(text, encoding="utf-8")
    pp = kok / "pyproject.toml"
    pp.write_text(re.sub(r'^version\s*=\s*"[^"]+"', f'version = "{version}"', pp.read_text(encoding="utf-8"),
                         count=1, flags=re.M), encoding="utf-8")
    print(f"{old} → {version}. Sonra: git commit -am 'Sürüm {version}' && git tag v{version} && "
          f"git push origin main v{version}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="komut", required=True)
    sub.add_parser("hazirla").add_argument("surum")
    sub.add_parser("denetle").add_argument("etiket")
    sub.add_parser("notlar").add_argument("surum")
    a = ap.parse_args(argv)
    if a.komut == "hazirla":
        prepare(a.surum)
    elif a.komut == "denetle":
        print(f"sürüm {check_tag(a.etiket)} tutarlı")
    else:
        sys.stdout.write(notes(a.surum) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
