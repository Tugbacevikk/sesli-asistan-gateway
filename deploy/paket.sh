#!/usr/bin/env bash
# İnternetsiz kurulum paketi üretir (tasarim.md 14.3): internetli bir makinede çalıştırılır, çıkan .tar.gz sunucuya
# taşınır ve `sudo ./deploy/install.sh --paket sesli-asistan-<sürüm>.tar.gz` ile kurulur.
#
#   deploy/paket.sh                       # Gateway + STT (GPU) + araçlar + varsayılan modeller
#   deploy/paket.sh --tts                 # kadın sesi servisinin paketleri de
#   deploy/paket.sh --cikti /yol/klasor
#
# İçerik: tekerlek/ (kilitli sürümlerin Linux x86_64 / Python 3.12 wheel'leri — kilit dosyasındaki SHA256 ile
# indirilir — ve projenin kendi wheel'i), models/ (SHA256 doğrulanmış), deploy/, tools/, lisanslar/ (her paketin
# lisans metni), THIRD_PARTY_LICENSES.md, SHA256SUMS. Ollama ve modeli pakete girmez (bkz. tasarim.md 14.3).
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
KOK=$(pwd)
CIKTI="$KOK/dist"
TTS=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --tts) TTS=1; shift ;;
        --cikti) CIKTI=$(realpath -m "$2"); shift 2 ;;
        -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
        *) echo "bilinmeyen seçenek: $1"; exit 1 ;;
    esac
done
command -v python3 >/dev/null || { echo "python3 gerekli"; exit 1; }
SURUM=$(grep -m1 '^version' pyproject.toml | cut -d'"' -f2)
if git rev-parse --short HEAD >/dev/null 2>&1; then
    [[ -z "$(git status --porcelain --untracked-files=no)" ]] || { echo "HATA: commit edilmemiş değişiklik var"; exit 1; }
    SURUM="$SURUM-$(git rev-parse --short HEAD)"
fi
AD="sesli-asistan-$SURUM"
IS=$(mktemp -d)
trap 'rm -rf "$IS"' EXIT
P="$IS/$AD"
mkdir -p "$P/tekerlek" "$P/models" "$CIKTI"

python3 tools/kurulum/lisanslar.py --kontrol >/dev/null || { echo "HATA: THIRD_PARTY_LICENSES.md kilitlerle uyumsuz"; exit 1; }

# Ubuntu 24.04 glibc 2.39: manylinux_2_5 … 2_39 ve eski adlar (pip --platform alt sürümleri kendisi saymaz)
PLATFORM=(--platform linux_x86_64 --platform manylinux1_x86_64 --platform manylinux2010_x86_64
          --platform manylinux2014_x86_64)
for g in $(seq 5 39); do PLATFORM+=(--platform "manylinux_2_${g}_x86_64"); done

KILITLER=(gateway stt-gpu stt araclar)
[[ $TTS -eq 1 ]] && KILITLER+=(tts)
for k in "${KILITLER[@]}"; do
    echo "==> wheel'ler: $k"
    python3 -m pip download -q --require-hashes -r "deploy/kilit/$k.txt" -d "$P/tekerlek" \
        --only-binary=:all: "${PLATFORM[@]}" --python-version 3.12 --implementation cp
done
echo "==> proje wheel'i"
python3 -m pip wheel -q --no-deps . -w "$P/tekerlek"

echo "==> modeller"
python3 -m venv "$IS/araclar"
"$IS/araclar/bin/pip" install -q --require-hashes -r deploy/kilit/araclar.txt
"$IS/araclar/bin/python" tools/models/indir.py --hedef "$P/models"

echo "==> lisans metinleri"
python3 - "$P/tekerlek" "$P/lisanslar" <<'PY'
import sys, zipfile, pathlib, re
src, dst = map(pathlib.Path, sys.argv[1:])
for whl in sorted(src.glob("*.whl")):
    pkg = whl.name.split("-")[0]
    with zipfile.ZipFile(whl) as z:
        for n in z.namelist():
            if re.search(r"\.dist-info/(licenses/.+|LICEN[CS]E[^/]*|COPYING[^/]*|NOTICE[^/]*)$", n, re.I):
                out = dst / pkg / pathlib.Path(n).name
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(z.read(n))
missing = sorted({w.name.split("-")[0] for w in src.glob("*.whl")} - {p.name for p in dst.iterdir()})
print("lisans metni bulunamayan (THIRD_PARTY_LICENSES.md'deki kaynağa bakın):", ", ".join(missing) or "yok")
PY

cp -r deploy tools THIRD_PARTY_LICENSES.md "$P/"
mkdir -p "$P/data" && cp data/rehber_ornek.json "$P/data/"
find "$P/tools" -name __pycache__ -prune -exec rm -rf {} +
echo "$SURUM" > "$P/SURUM"
(cd "$P" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)
tar -C "$IS" -czf "$CIKTI/$AD.tar.gz" "$AD"
(cd "$CIKTI" && sha256sum "$AD.tar.gz" > "$AD.tar.gz.sha256")
echo "==> $CIKTI/$AD.tar.gz ($(du -h "$CIKTI/$AD.tar.gz" | cut -f1))"
echo "    sunucuda: tar -xzf $AD.tar.gz && sudo $AD/deploy/install.sh --paket $AD.tar.gz"
