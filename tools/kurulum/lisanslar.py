"""Dağıtım paketindeki üçüncü taraf bileşenlerin lisans listesi: THIRD_PARTY_LICENSES.md (tasarim.md 14.5).

Python paketleri `deploy/kilit/*.txt`'deki **sabit sürümlerden** okunur; lisans bilgisi PyPI'daki o sürümün
üst verisinden (License-Expression, License, sınıflandırıcılar) alınır. Modeller ve ayrı servisler
`MODELLER` tablosundan gelir (elle bakımlı; kaynak: deploy/models.yaml ve tasarim.md 14.5).

Hukuki inceleme gerektirenler ayrıca işaretlenir: GPL/LGPL/AGPL, ticari olmayan (NC), bilinmeyen.

    python tools/kurulum/lisanslar.py              # yaz
    python tools/kurulum/lisanslar.py --kontrol    # kilit dosyalarıyla uyumlu mu (CI): değilse çıkış kodu 1
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCKS = ROOT / "deploy" / "kilit"
OUT = ROOT / "THIRD_PARTY_LICENSES.md"
SERVICES = {"gateway": "Gateway", "stt": "STT", "stt-gpu": "STT", "tts": "TTS (Seslen)", "araclar": "Kurulum araçları"}

MODELLER = [
    ("faster-whisper-medium (Systran, CTranslate2 dönüşümü)", "STT modeli", "MIT",
     "https://huggingface.co/Systran/faster-whisper-medium"),
    ("Whisper medium ağırlıkları (OpenAI)", "STT modeli", "MIT", "https://github.com/openai/whisper"),
    ("Silero VAD (ONNX)", "Konuşma algılama", "MIT", "https://github.com/snakers4/silero-vad"),
    ("Qwen3.5 4B (Ollama)", "Dil modeli", "Apache-2.0 (model kartında teyit edilir)", "https://ollama.com/library/qwen3.5"),
    ("Ollama", "LLM çalıştırıcı (ayrı servis)", "MIT", "https://github.com/ollama/ollama"),
    ("Piper (piper1-gpl) + espeak-ng", "TTS (ayrı süreç, mevcut servis)", "GPL-3.0 — yalnızca ayrı süreç (C-05)",
     "https://github.com/OHF-Voice/piper1-gpl"),
    ("Seslen TTS model ağırlıkları ve sesleri", "Kadın sesi (ADR-015, isteğe bağlı)",
     "CC BY-SA 4.0 — atıf + aynı lisansla paylaşım; yapay zekâ sesi olduğu belirtilir", "https://huggingface.co/seslen-tr"),
    ("NVIDIA cuBLAS / cuDNN (pip)", "STT GPU kütüphaneleri",
     "NVIDIA Software License — yeniden dağıtım şartları hukukça teyit edilmeli", "https://docs.nvidia.com/cuda/eula/"),
]
REVIEW = re.compile(r"\b(A?GPL|LGPL|NC|Non-?Commercial|NVIDIA|Proprietary|bilinmiyor|UNKNOWN)\b", re.I)


def locked(path: Path) -> list[tuple[str, str]]:
    return [(m.group(1).lower(), m.group(2)) for m in re.finditer(r"^([A-Za-z0-9_.\-]+)==([^\s\;]+)",
                                                                  path.read_text(), re.M)]


def license_of(name: str, version: str) -> str:
    try:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=20) as r:
            info = json.load(r)["info"]
    except Exception as e:
        return f"bilinmiyor ({type(e).__name__})"
    if expr := (info.get("license_expression") or "").strip():
        return expr
    classifiers = [c.split(" :: ")[-1] for c in info.get("classifiers") or [] if c.startswith("License ::")]
    classifiers = [c for c in classifiers if c != "OSI Approved"]
    if classifiers:
        return ", ".join(dict.fromkeys(classifiers))
    lic = (info.get("license") or "").strip()
    if lic and len(lic) < 80 and "\n" not in lic:
        return lic
    return "bilinmiyor (PyPI üst verisinde yok; paketin LICENSE dosyasına bakın)"


def build() -> str:
    pkgs: dict[tuple[str, str], set[str]] = {}
    for f in sorted(LOCKS.glob("*.txt")):
        for p in locked(f):
            pkgs.setdefault(p, set()).add(SERVICES.get(f.stem, f.stem))
    with ThreadPoolExecutor(8) as ex:
        lic = dict(zip(pkgs, ex.map(lambda p: license_of(*p), pkgs), strict=True))
    lines = ["# Üçüncü taraf bileşenler ve lisansları", "",
             "Bu dosya `python tools/kurulum/lisanslar.py` ile `deploy/kilit/*.txt`'deki sabit sürümlerden üretilir;",
             "elle değiştirmeyin. Lisans metinleri dağıtım paketinde `lisanslar/` klasöründedir (kurulu paketlerin",
             "`*.dist-info/licenses` dosyaları). ⚠ işaretliler dağıtımdan önce hukuk incelemesi gerektirir.", "",
             "## Modeller ve ayrı servisler", "", "| Bileşen | Rol | Lisans | Kaynak |", "|---|---|---|---|"]
    for name, role, license_, url in MODELLER:
        mark = "⚠ " if REVIEW.search(license_) else ""
        lines.append(f"| {name} | {role} | {mark}{license_} | {url} |")
    lines += ["", "## Python paketleri", "", "| Paket | Sürüm | Lisans | Servis |", "|---|---|---|---|"]
    for (name, ver), svc in sorted(pkgs.items()):
        mark = "⚠ " if REVIEW.search(lic[(name, ver)]) else ""
        lines.append(f"| {name} | {ver} | {mark}{lic[(name, ver)]} | {', '.join(sorted(svc))} |")
    flagged = sorted(n for (n, v) in pkgs if REVIEW.search(lic[(n, v)]))
    lines += ["", f"İnceleme gereken paketler: {', '.join(flagged) if flagged else 'yok'}.", ""]
    return "\n".join(lines)


def check() -> int:
    """Ağsız denetim: dosyadaki paket/sürüm listesi kilit dosyalarıyla aynı mı."""
    text = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
    have = set(re.findall(r"^\| ([a-z0-9_.\-]+) \| ([^ |]+) \|", text, re.M))
    want = {p for f in LOCKS.glob("*.txt") for p in locked(f)}
    if have != want:
        print("THIRD_PARTY_LICENSES.md güncel değil: python tools/kurulum/lisanslar.py")
        for n, v in sorted(want - have):
            print(f"  eksik: {n}=={v}")
        for n, v in sorted(have - want):
            print(f"  fazla: {n}=={v}")
        return 1
    print("lisans listesi güncel")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kontrol", action="store_true")
    a = ap.parse_args(argv)
    if a.kontrol:
        return check()
    OUT.write_text(build(), encoding="utf-8")
    print(f"yazıldı: {OUT.relative_to(ROOT)}")
    return check()


if __name__ == "__main__":
    sys.exit(main())
