#!/usr/bin/env bash
# Eğitilmiş GGUF'u Ollama'ya yükler. Sohbet şablonu, durdurma sözcükleri ve ayrıştırıcı ana modelden
# alınır: eğitimdeki biçimle üretimdeki biçim aynı kalsın (şablon farkı en sık bozulma nedeni).
#
#   bash tools/egitim/ollama_yukle.sh yol/gemma-santral.Q8_0.gguf [model_adi] [ana_model]
#   ana_model: eğitilen modelin Ollama'daki karşılığı — gemma3:1b (varsayılan) ya da gemma4:e4b
set -euo pipefail

GGUF="$(realpath "${1:?GGUF dosyasının yolunu verin}")"
AD="${2:-gemma-santral}"
ANA="${3:-gemma3:1b}"

ollama pull "$ANA"
MF="$(mktemp)"
# ana modelin Modelfile'ı: yalnız FROM satırı bizim dosyamızla değişir
ollama show "$ANA" --modelfile | sed "s|^FROM .*|FROM $GGUF|" > "$MF"
# santral: kısa ve kararlı çıktı (üretimde chat_json zaten temperature 0 verir)
echo "PARAMETER temperature 0" >> "$MF"
ollama create "$AD" -f "$MF"
rm -f "$MF"

echo
echo "Deneme:"
ollama run "$AD" --think=false "merhaba" || ollama run "$AD" "merhaba"
echo
echo "Ölçüm:  python tools/egitim/anlama_olc.py --model $ANA --model $AD"
