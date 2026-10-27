#!/usr/bin/env bash
# Sabit sürüm + SHA256 kilit dosyaları (deploy/kilit/*.txt), hedef: Ubuntu 24.04, Python 3.12, x86_64.
# Kurulum bunları `pip install --require-hashes` ile kullanır: aynı sürüm, değiştirilmemiş paket.
# pyproject.toml'daki bağımlılık değişince çalıştırın, ardından: python tools/kurulum/lisanslar.py
set -euo pipefail
cd "$(dirname "$0")/../.."
command -v uv >/dev/null || { echo "uv gerekli: pip install uv"; exit 1; }
mkdir -p deploy/kilit
derle() {   # derle <çıktı> <extra...>
    local out=$1; shift
    local args=()
    for e in "$@"; do args+=(--extra "$e"); done
    uv pip compile pyproject.toml "${args[@]}" --python-version 3.12 --python-platform x86_64-manylinux_2_28 \
        --generate-hashes --no-header --quiet -o "deploy/kilit/$out.txt"
    echo "deploy/kilit/$out.txt: $(grep -c '==' "deploy/kilit/$out.txt") paket"
}
derle gateway gateway
derle stt stt
derle stt-gpu stt stt-cuda
derle tts tts
derle araclar tools
