#!/usr/bin/env bash
# Dağıtım Modelfile'ını üretir: deploy/modelfiles/<ad>.Modelfile (ADR-038). Sohbet şablonu, durdurma sözcükleri ve
# lisans metni (Gemma Terms of Use) ana modelden alınır: eğitimdeki biçimle kurulumdaki biçim aynı kalsın.
# install.sh kurulumda FROM satırını indirilen GGUF'un yoluyla değiştirir; GGUF repoya girmez (şirket dosya sunucusu).
#
#   bash tools/egitim/modelfile_uret.sh [ad] [ana_model] [gguf_adi]
set -euo pipefail

AD="${1:-gemma-santral}"
ANA="${2:-gemma3:1b}"
GGUF="${3:-gemma-santral.Q8_0.gguf}"
OUT="deploy/modelfiles/$AD.Modelfile"

mkdir -p deploy/modelfiles
ollama pull "$ANA" >/dev/null
GOVDE="$(ollama show "$ANA" --modelfile | grep -v '^#' | sed "s|^FROM .*|FROM ./$GGUF|")"
{
    echo "# $AD — ince ayarlı $ANA (ADR-038, tools/egitim/). Şablon, durdurma sözcükleri ve lisans $ANA'dan"
    echo "# ($(date +%d.%m.%Y), 'ollama show $ANA --modelfile'). FROM satırını install.sh kurulumda değiştirir."
    echo "$GOVDE"
    # anlama çağrısı (chat_json) zaten temperature 0 verir; araçlı / sohbet çağrıları da kararlı olsun
    grep -q '^PARAMETER temperature' <<<"$GOVDE" || echo "PARAMETER temperature 0"
} > "$OUT"
grep -q '^TEMPLATE' "$OUT" || { echo "HATA: $OUT içinde TEMPLATE yok" >&2; exit 1; }
echo "yazıldı: $OUT ($(wc -l < "$OUT") satır)"
