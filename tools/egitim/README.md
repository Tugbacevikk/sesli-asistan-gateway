# Gemma ince ayarı (anlama modeli): Gemma 3 1B / Gemma 4 E4B

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
| 1b | kendi makinen | **özel isim denetimi:** `python tools/egitim/ozel_isim_denetim.py --katı` → "BULGU YOK" görmeden eğitime başlama |
| 2 | kendi makinen | **eğitimden önce taban ölçüm:** `python tools/egitim/anlama_olc.py --model qwen3.5:4b --model gemma3:1b` |
| 3 | Colab (T4) | `egit_gemma.ipynb`'yi aç, `MODEL` seç, `anlama_egitim.jsonl` + `anlama_dogrulama.jsonl` yükle, sırayla çalıştır |
| 4 | kendi makinen | `bash tools/egitim/ollama_yukle.sh gemma-santral.Q8_0.gguf gemma-santral gemma3:1b` |
| 5 | kendi makinen | `python tools/egitim/anlama_olc.py --model gemma3:1b --model gemma-santral` |
| 6 | kendi makinen | uçtan uca: `python tests/eval/diyalog.py --anlama --model gemma-santral --set tests/data/diyalog_seti_serbest.yaml --rehber data/sirket_rehber.json` |

Eğitim Colab'da ya da GPU'lu sunucuda yapılır (laptop GPU'su 4 GB). Ölçüm (adım 2, 5, 6) laptopta çalışır:
Gemma 3 1B Q8 ~1 GB, GPU'ya sığar; E4B Q4 ~5 GB, kısmen CPU'ya taşar, yavaş ama doğru sonuç verir.
Gecikme kararı sunucu donanımında ölçülmelidir.

## Veri

`anlama_veri.py` örnekleri kurala göre kurar: söz, rehberdeki bilinen bir kayıttan üretilir, etiket de o
sırada bilinir. Sonra **her örnek gerçek diyalog ajanından geçirilir** (LLM yerine etiketi dönen sahte LLM);
etiket yanlış kişiye aktarıyorsa ya da hiçbir yere varmıyorsa örnek atılır.

- Türler: ad+soyad, birim+ad+hitap, ad+unvan, birim, unvan, birim+unvan, tarif ("muhasebenin başındaki kişi"),
  dahili (rakam ve yazıyla), konu (üretimdeki konu tablosuyla), dışlama ("muhasebe değil satış"), operatör,
  kapat, mesai, selam, farketmez, belirsiz (istem saldırısı, konu dışı).
- v8: **sohbet** (ayrı görev, ADR-035): sistem `SMALL_TALK_PROMPT`, model tek sıcak cümle ("Günaydın, iyiyim,
  teşekkür ederim."). Eğitim ve doğrulamaya karışır; testi ayrı dosyada (`sohbet_test.jsonl`), `anlama_olc.py`
  kendiliğinden ölçer (denetimden geçen %, selam karşılığı %).
- STT bozulmaları: Türkçe harf kaybı (`ayse hanim`), "alo / eee / iyi günler" başı, "lütfen" sonu.
- Dengeleme: bir rehberde bir türden en çok `--tur-siniri` (250) örnek.
- Sistem istemi üretimdekiyle birebir aynı (`UNDERSTAND_PROMPT` + rehberin birim listesi).
- Özel isimler: bütün kişi adları sentetik rehber üreticilerinin ortak Türkçe ad havuzundan gelir (gerçek kişi
  değil). Rehberlerde geçen gerçek yer adları (kamu test rehberindeki ilçe kaymakamlıkları) veri üretilirken
  sabit bir eşlemeyle uydurma adlara çevrilir. `ozel_isim_denetim.py` her üretimden sonra veriyi tarar: gerçek
  il / ilçe adı, şirket adı (`YASAKLI`, `--yasakli dosya.txt` ile ek liste), telefon / e-posta / T.C. kimlik /
  IBAN kalıbı ve havuz dışı ad arar.

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
- **Gemma 3 1B ve araçlar:** Ollama, araç şablonu olmayan modele `tools` alanlı isteği reddedebilir. Gateway'in
  araçlı LLM turları (`LlmMixin._run`) bu modelle hata verirse kural tabanlı tura düşer (`llm_fallback`).
  Adım 6'da `LLM hatası` sütununa bakın; sıfır değilse araçlı turlar için ayrı model gerekir.
- **Gemma 3 1B ve Türkçe:** model kartı 1B'nin çok dilli puanlarını düşük veriyor; ince ayar dar görevde bunu
  kapatabilir ama ölçmeden varsayılmamalı (adım 2 → 5 farkı).
- Lisans: Gemma 3 "Gemma Terms of Use" (yasaklı kullanım politikası var; Apache değil), Gemma 4 Apache 2.0.
  Eğitilmiş ağırlıkları dağıtırken `THIRD_PARTY_LICENSES.md`'ye eklenmeli.

## Dağıtım (ADR-038)

1. GGUF'u şirketin dosya sunucusuna koy: `<mirror>/gemma-santral/gemma-santral.Q8_0.gguf`; `deploy/models.yaml`'da
   `mirror` adresini ve yeni modelin `sha256`'sını güncelle.
2. `bash tools/egitim/modelfile_uret.sh` → `deploy/modelfiles/gemma-santral.Modelfile` (şablon + Gemma lisans metni
   `gemma3:1b`'den). Commit et; GGUF repoya girmez.
3. `deploy/ayar/gateway.env`: `LLM_MODEL=gemma-santral`. `install.sh` GGUF'u indirir, özeti doğrular, `ollama create`
   ile kurar; indiremezse uyarır, LLM adımları kural tabanlı sürer. İnternetsiz paket (`paket.sh`) GGUF'u da içerir.
