# Gemma 4 E4B ince ayarı (anlama modeli)

Neyi eğitiyoruz: **anlama kipi** (`UNDERSTAND_MODE=llm`, ADR-037). Kuralların çözemediği söz LLM'e gider,
LLM sözü JSON'a çevirir (`{"niyet":"aktar","unvan":"müdür","birim":"muhasebe"}`), karar ve aktarma yine kodda kalır.

Neden bu görev:
- Üretimde LLM'in asıl işi bu (`deploy/ayar/gateway.env`: `UNDERSTAND_MODE=llm`).
- Çıktı kısa ve etiketi kesin: küçük model bu tür dar bir işte iyi öğrenir, gecikme de kısa çıktıyla düşer
  (ölçüm 11.11.2026: qwen3.5:4b ile çağrı ~4,9 sn).
- Araç çağrısı yok: Ollama'nın Gemma 4 araç ayrıştırıcısındaki bilinen hatalardan etkilenmez.

Neyi eğitmiyoruz: **rehberin kendisi**. Ad, dahili ve birim bilgisi modele gömülmez; her kurum kendi rehberiyle
gelir. Bu yüzden test seti eğitimde hiç görülmemiş bir rehberdir (`demo`): model yeni bir kuruma genelleniyor mu,
onu ölçer.

## Adımlar

| # | Nerede | Ne |
|---|--------|----|
| 1 | kendi makinen | `python tools/egitim/anlama_veri.py` → `tools/egitim/veri/*.jsonl` (~3 dk, GPU gerekmez) |
| 2 | kendi makinen | **eğitimden önce taban ölçüm:** `python tools/egitim/anlama_olc.py --model qwen3.5:4b --model gemma4:e4b` |
| 3 | Colab (T4) | `egit_gemma4_e4b.ipynb`'yi aç, `anlama_egitim.jsonl` + `anlama_dogrulama.jsonl` yükle, sırayla çalıştır |
| 4 | kendi makinen | `bash tools/egitim/ollama_yukle.sh gemma4-santral.Q4_K_M.gguf` |
| 5 | kendi makinen | `python tools/egitim/anlama_olc.py --model gemma4:e4b --model gemma4-santral` |
| 6 | kendi makinen | uçtan uca: `python tests/eval/diyalog.py --anlama --model gemma4-santral --set tests/data/diyalog_seti_serbest.yaml --rehber data/sirket_rehber.json` |

Laptop GPU'su (4 GB) eğitime yetmez; E2B bile 8 GB ister. Eğitim Colab'da ya da GPU'lu sunucuda yapılır.
Ölçüm (adım 2, 5, 6) laptopta çalışır: E4B Q4 ~5 GB, kısmen CPU'ya taşar, yavaş ama doğru sonuç verir.
Gecikme kararı sunucu donanımında ölçülmelidir.

## Veri

`anlama_veri.py` örnekleri kurala göre kurar: söz, rehberdeki bilinen bir kayıttan üretilir, etiket de o
sırada bilinir. Sonra **her örnek gerçek diyalog ajanından geçirilir** (LLM yerine etiketi dönen sahte LLM);
etiket yanlış kişiye aktarıyorsa ya da hiçbir yere varmıyorsa örnek atılır.

- Türler: ad+soyad, birim+ad+hitap, ad+unvan, birim, unvan, birim+unvan, tarif ("muhasebenin başındaki kişi"),
  dahili (rakam ve yazıyla), konu (üretimdeki konu tablosuyla), dışlama ("muhasebe değil satış"), operatör,
  kapat, mesai, selam, farketmez, belirsiz (istem saldırısı, konu dışı).
- STT bozulmaları: Türkçe harf kaybı (`ayse hanim`), "alo / eee / iyi günler" başı, "lütfen" sonu.
- Dengeleme: bir rehberde bir türden en çok `--tur-siniri` (250) örnek.
- Sistem istemi üretimdekiyle birebir aynı (`UNDERSTAND_PROMPT` + rehberin birim listesi).

İstem (`understand.py`) değişirse veri yeniden üretilmeli ve model yeniden eğitilmelidir: model istemin
biçimine alışır.

## Ölçütler (`anlama_olc.py`)

- **tam %**: bütün alanlar etiketle aynı. Katı ölçüt; modelin ne öğrendiğini gösterir.
- **uçtan uca %**: modelin çıktısı ajandan geçince arayan doğru yere gidiyor mu. Asıl ölçüt bu, ama hoşgörülü:
  model "belirsiz" dese de kurallar çoğu sözü yine çözer. Bu yüzden ikisine birlikte bakın.
- **p50 / p95**: çağrı süresi. Sunucuda ölçülmeli.

Kabul önerisi: test setinde (demo) uçtan uca ≥ taban modeldeki, tam % belirgin şekilde yüksek, p95 düşük;
diyalog setlerinde (adım 6) izinsiz aktarma 0.

## Dikkat

- Aynı `LLM_MODEL` araçlı LLM turlarında da (santral olayı sonrası konuşma, small talk) kullanılır. Yalnız JSON'la
  eğitilen model sohbette bozulabilir. Adım 6'yı bütün setlerle çalıştırın; bozulma varsa iki seçenek:
  veriye sohbet turları eklemek ya da anlama için ayrı model adı (koda küçük bir ayar).
- Şablon: GGUF Ollama'ya ana modelin (`gemma4:e4b`) şablonuyla yüklenir (`ollama_yukle.sh`). Eğitilmiş model
  Colab'da iyi, Ollama'da kötüyse ilk şüpheli şablon farkıdır.
- Lisans: Gemma 4 Apache 2.0. Eğitilmiş ağırlıkları dağıtırken `THIRD_PARTY_LICENSES.md`'ye eklenmeli.
