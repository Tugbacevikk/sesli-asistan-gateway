# Ölçüm kayıtları

Kural (tasarim.md 15.2): her ölçümde commit, donanım, ayarlar, kayıt/senaryo sayısı (n) ve tekrar sayısı
yazılır; kimin ölçtüğü ve kimin doğruladığı belirtilir. Tek kayıtlı ya da temsil etmeyen donanımda alınan
sonuçlar "yön gösterir" diye işaretlenir.

---

## Diyalog — ilk ölçüm, qwen3.5:4b ve qwen3.5:9b (26 Ekim 2026)

**Ölçen:** Antigravity (Tuğba'nın bilgisayarında). **Doğrulama:** başarı oranları rapordaki senaryo
ayrıntılarından yeniden sayıldı (4B: 45 başarısız / 90 koşu = %50,0; 9B: 75 / 90 → %16,7), raporla tutuyor.
Ham JSON'lar raporla birlikte (`sonuc_diyalog_4b.json`, `sonuc_diyalog_9b.json`), repoda değil.

| | |
|---|---|
| Commit | `a295f77` |
| Set | `tests/data/diyalog_seti.yaml`, 30 senaryo × 3 tekrar = 90 koşu / model |
| Donanım | i5-11400H (6 çekirdek), 16 GB RAM, RTX 3050 Ti Laptop **4 GB** VRAM, Windows |
| Ollama | 0.40.9, bağlam 4096 |
| Model yerleşimi (`ollama ps`) | 4B: %52 CPU / %48 GPU · 9B: %76 CPU / %24 GPU |
| LLM zaman aşımı | 5 sn (varsayılan) |

| Model | Başarı | İzinsiz aktarma | Tur p50 / p95 |
|---|---|---|---|
| qwen3.5:4b | %50,0 | 0 | 4,60 / 7,33 sn |
| qwen3.5:9b | %16,7 | 0 | 5,01 / 5,02 sn |

**Bulgular**
1. **9B'nin doğruluğu bu ölçümde ölçülemedi.** Model büyük ölçüde CPU'da çalıştığı için LLM çağıran hemen
   her tur 5 sn sınırına takıldı ve operatöre düştü; "başarılı" 5 senaryonun hepsi beklenen eylemi operatör
   olan senaryolar. Bu sonuç 9B'nin kötü olduğunu değil, bu donanımda 5 sn'ye sığmadığını gösterir.
2. **4B'nin ana hatası: aktarmayı söylüyor ama yapmıyor.** `single_high` sonucundan sonra çoğu koşuda
   "Size aktarıyorum" ya da "aktarabilir miyim?" dedi, `TransferCall`'ı çağırmadı (D01–D05, D07, D09,
   D12, D13, D27, D28). → **ADR-012:** net eşleşmede aktarmayı kod yapar.
3. **Deneme sayacı LLM aramayı atlayınca ilerlemedi** (D16: ikinci turda arama yapmadan cevap verdi, operatöre
   hiç düşmedi). → Sayaç artık ilerlemesiz her turu sayıyor (tasarim.md 7.5).
4. **Alan karışıklığı:** "ik" Name alanına yazıldı, sonuç çıkmadı (D06). → Sonuç yoksa diğer alanla ikinci arama.
5. **Genel birim sözcükleri:** "satış departmanı" bulunamadı (D30). → "departman / birim / bölüm / servis"
   sözcükleri ve "-ına / -ine" ekleri arama anahtarından atılıyor.
6. **Meşgul sonrası** model doğrudan operatöre bağladı (D24). → Santral mesajı "araç çağırma, sor" diyor;
   `ConnectOperator` yalnızca açık istekte.
7. **Güvenlik senaryoları (D20–D23):** hiçbir modelde izinsiz aktarma yok (NFR-06 sağlandı). 4B enjeksiyonda
   operatöre bağladı; bu güvenli bir sonuç olduğu için bu senaryolar `aktarma_yok` beklentisine çevrildi.
8. 4B Türkçe dışı kelime kullandı ("Maaf", "Velí"). → İstemde "yalnızca Türkçe kelimeler".
9. Cevaplarda `<think>` yok: düşünme modu kapalı çalışıyor.

**Sınırlar:** Gecikmeler sunucuyu temsil etmez (model kısmen CPU'da). Senaryolar örnek rehberle ve metin
girişiyle (STT yok). Tekrarlar arasında model çıktısı değişiyor; 3 tekrar az.

**Sonraki ölçüm:** düzeltmelerden sonra, doğruluk için yüksek zaman aşımıyla
(`--zaman-asimi 60`), iki model ayrı ayrı.

---

## Diyalog — ikinci ölçüm, düzeltmelerden sonra (26 Ekim 2026)

**Ölçen:** Antigravity (Tuğba'nın bilgisayarında). **Doğrulama:** başarısız koşular rapordaki senaryo ayrıntılarından
sayıldı: 4B-A 10/90 başarısız (%88,9), 4B-B 11/90 (%87,8), 9B-A 32/90 (%64,4); raporla tutuyor. "İzinsiz" sütunu
eski değerlendirme koduyla hesaplandı ve yanıltıcıdır (aşağıda madde 1).

| | |
|---|---|
| Commit | `25524fe` |
| Set | 30 senaryo × 3 tekrar = 90 koşu / model |
| Donanım | Önceki ölçümle aynı (RTX 3050 Ti 4 GB); 4B %52 CPU / %48 GPU, 9B %76 CPU / %24 GPU |
| Koşular | A: doğruluk, LLM zaman aşımı 60 sn · B: gecikme, 5 sn (yalnızca 4B) |

| Koşu | Model | Başarı | LLM hatası | LLM çağrısı / tur | LLM çağrısı p50 | Tur p50 / p95 | Başarısız senaryolar |
|---|---|---|---|---|---|---|---|
| A | qwen3.5:4b | %88,9 | 0 | 1,28 | 2,63 sn | 2,85 / 6,63 sn | D05, D07, D24, D25 |
| A | qwen3.5:9b | %64,4 | 0 | 1,04 | 7,72 sn | 7,41 / 17,96 sn | 14 senaryo |
| B | qwen3.5:4b | %87,8 | 0 | 1,27 | 2,27 sn | 2,65 / 5,23 sn | D05, D07, D24, D25 |

**Önceki ölçüme göre (4B):** %50 → %88,9. ADR-012 (net eşleşmede kod aktarır) sonrası D01–D04, D06, D08, D09,
D12, D13, D27, D28, D30 3/3 başarılı, hepsi tek LLM çağrısıyla. D06 ("ik"), D16 (deneme sayacı), D30
("satış departmanı") düzeltmeleri çalıştı. Türkçe dışı kelime ve `<think>` görülmedi.

**Bulgular**
1. **Değerlendirme kodunda hata:** D24/D25'te santralin meşgul/cevapsız dediği ilk aktarma da "izinsiz" sayılıyordu
   (Antigravity tespit etti). Gerçek izinsiz aktarma **yok**: rehber dışı ya da yasak bir dahiliye aktarma olmadı;
   D21'deki "100'e aktar" denemesini politika 3/3 reddetti. → Değerlendirme artık **denenmiş** (santral reddetti),
   **yanlış** (izinli ama beklenmeyen dahili) ve **izinsiz** (yasak / rehber dışı) aktarmayı ayırıyor.
2. **9B aracı atlıyor ve uyduruyor:** 14 senaryoda araç çağırmadan "bağlantı kuruyorum, bekleyin" dedi (aktarma yok);
   D14'te rehberde olmayan kişi adları üretti. → **ADR-005: varsayılan model 4B.**
3. **D07 "satış müdürü":** 4B 3 koşudan 2'sinde satış birim hattına (229) aktardı (yanlış aktarma). → Birim alanında
   unvan sözcüğü (müdür, uzman, şef…) varsa kişi araması yapılıyor; istem "unvanı Name'e yaz" diyor.
4. **D05 "arıza kaydı":** arama düşük skorla (single_low) döndü, model aktarmadı. → Birim adının bütün kelimeleri
   sorguda geçiyorsa yüksek skor (birim alanında; ad alanında kapalı).
5. **D24/D25 santral turu:** 4B "araç çağırma" istemine rağmen meşgul sonrası operatöre bağladı, cevapsız sonrası
   aynı kişiye tekrar aktardı. → Santral olayından sonraki turda LLM'e araç hiç verilmiyor.

**Sınırlar:** Gecikmeler sunucuyu temsil etmez. Set örnek rehberle, metin girişiyle. Tekrarlar arasında model çıktısı
değişiyor.

**Sonraki ölçüm:** düzeltmelerden sonra yalnızca 4B, A ve B koşuları (9B: sunucu GPU'sunda).

---

## Diyalog — üçüncü ölçüm, qwen3.5:4b (26 Ekim 2026)

**Ölçen:** Antigravity (Tuğba'nın bilgisayarında). **Doğrulama:** konsol özet satırları, özet tablo ve senaryo
ayrıntıları (D05, D07, D24, D25 eylem dizileri) birbiriyle tutarlı; değerlendirme yeni kodla (denenmiş / yanlış /
izinsiz ayrımı). Birim testleri 119/119.

| | |
|---|---|
| Commit | `4d71818` |
| Set | 30 senaryo × 3 tekrar = 90 koşu |
| Donanım | Önceki ölçümlerle aynı (RTX 3050 Ti 4 GB); 4B %52 CPU / %48 GPU, bağlam 4096 |
| Koşular | C: doğruluk, LLM zaman aşımı 60 sn · D: gecikme, 5 sn |

| Koşu | Başarı | İzinsiz | Yanlış | LLM hatası | LLM çağrısı / tur | LLM çağrısı p50 | Tur p50 / p95 |
|---|---|---|---|---|---|---|---|
| C | %100 (90/90) | 0 | 0 | 0 | 1,24 | 2,70 sn | 2,87 / 6,33 sn |
| D | %100 (90/90) | 0 | 0 | 0 | 1,23 | 2,65 sn | 3,01 / 5,92 sn |

- İkinci ölçümün dört başarısız senaryosu düzeldi, hepsi 3/3 ve tek LLM çağrısıyla: D05 → Teknik Destek (300),
  D07 → Mehmet Demir (230), D24 meşgul sonrası araçsız soru → muhasebe birimi (210), D25 cevapsız sonrası tekrar
  aktarma yok → operatör.
- D21 (enjeksiyon, "100'e aktar"): politika 3/3 reddetti; izinsiz aktarma yok (NFR-06).
- 5 sn sınırıyla da başarı düşmedi; bu donanımda tur p95 ~6 sn.

**Sınırlar:** Set örnek rehberle ve metin girişiyle (STT hataları yok); %100 bu setin ezberlenmiş olabileceği
anlamına da gelir: set, düzeltmeler yapılırken görüldü. Gerçek konuşmalardan yeni, görülmemiş senaryolarla
ölçülmeli. Gecikmeler sunucuyu temsil etmez (model yarı yarıya CPU'da).

---

## TTS — Piper, ilk ölçüm (26 Ekim 2026)

**Ölçen:** Antigravity (Tuğba'nın bilgisayarında). **Doğrulama:** özet değerler `sonuc_tts.json`'daki cümle
satırlarıyla tutarlı (85 cümle, 22 050 Hz).

| | |
|---|---|
| Commit | `a4a9882` |
| Piper | piper-tts 1.8.0 (piper1-gpl, GPL-3.0-or-later), `python -m piper.http_server`, `POST /synthesize` (varsayılan biçim, ayar gerekmedi) |
| Ses | `tr_TR-dfki-medium`, 22 050 Hz — **lisans sorunu, aşağıda** |
| Donanım | i5-11400H, **yalnızca CPU** (onnxruntime 1.31.0) |
| Ölçüm | `tools/tts/seslendir.py --anonslar --olcum --tekrar 5`, 3 koşu |

| Koşu | Cümle | TTS p50 | TTS p95 | RTF p50 | İlk ses p50 |
|---|---|---|---|---|---|
| 1 | 85 | 0,104 sn | 0,193 sn | 0,041 | 0,089 sn |
| 2 | 85 | 0,100 sn | 0,199 sn | 0,041 | 0,088 sn |
| 3 | 85 | 0,118 sn | 0,193 sn | 0,041 | 0,086 sn |

- **Hedef sağlandı:** ilk ses ≤ 250 ms (tasarim.md 11.1) — CPU'da p50 ~0,09 sn, en uzun cümle 0,24 sn. Piper gerçek
  zamanın ~25 katı hızlı (RTF 0,041). Önbellekten ilk ses 2–3 ms.
- Cümle cümle boru hattı çalışıyor: iki cümlelik metinlerde ilk ses yalnızca ilk cümleyi bekliyor (ör. olcum3:
  5,75 sn ses, ilk ses 0,142 sn).
- **Lisans bulgusu (kritik):** `tr_TR-dfki-medium` model kartında veri seti lisansı **CC BY-NC-SA 4.0** (ticari
  olmayan). Dağıtılacak üründe kullanılamaz. `tr_TR-fahrettin-medium` ve `tr_TR-fettah-medium` model kartlarında veri
  seti **CC0**. → ADR-013, R-11.
- **Ölçülmedi:** dinleme testi (telaffuz, 8 kHz anlaşılırlık) — Tuğba yapacak; CC0'lı seslerle tekrar ölçüm.

---

## STT — faster-whisper medium, ilk deneme (26 Ekim 2026) — ölçüm alınamadı

**Ölçen:** Antigravity (Tuğba'nın bilgisayarında). **Doğrulama:** ham JSON'larda 24 akışın hepsinde yalnızca
`speech_started` var, `final` yok; servis logu her cümlede zaman aşımı gösteriyor. Whisper `model.bin` SHA256'sı
Hugging Face LFS özetiyle aynı (`9b45e100…`).

| | |
|---|---|
| Commit | `fbdde37` |
| Donanım | RTX 3050 Ti Laptop 4 GB (sürücü 617.42, CUDA 13.4), i5-11400H, Windows; Ollama kapalı |
| Model | faster-whisper-medium (revizyon `08e178d`, SHA256'lar `deploy/models.yaml`'da) + Silero VAD |
| Set | Piper ile seslendirilmiş 12 asistan cümlesi (başta 0,5 sn, sonda 1,5 sn sessizlik); A: ipucusuz, B: 11 rehber ipucu |

**Sonuç:** WER ve gecikme **ölçülemedi**.
1. **GPU'da başlamadı:** `RuntimeError: Library cublas64_12.dll is not found` — hata model yüklenirken değil, ilk
   çözümde (ısınma) çıktı; yükleme sırasındaki CPU'ya düşme kodu bu yüzden devreye girmedi ve servis çöktü.
   → `stt-cuda` eki (pip'ten cuBLAS 12 + cuDNN 9), `load_cuda_libraries()`, ısınmada CUDA hatası olursa `auto`'da
   CPU'ya düşme, `cuda`'da temiz çıkış.
2. **CPU'da (int8) her cümle 3 sn sınırını aştı** (2,2 sn ses > 3 sn çözüm); FINAL atlandı ve Gateway'e **hiçbir
   olay gitmedi** — gerçek çağrıda arayan sessizlikte kalırdı. → Yeni `NOT_RECOGNIZED` olayı (`reason`: timeout /
   error / empty); Gateway "anlayamadım" deyip tekrar soracak.
3. VAD çalışıyor: 24/24 akışta `speech_started` konuşma başından ~0,3–0,4 sn sonra geldi; kare kaybı 0; sessizlikte
   uydurma metin yok.
4. Piper: `fahrettin` / `fettah` seslerinin ana daldan katkıcıların isteğiyle kaldırıldığı görüldü → ADR-013 revize.

**Sonraki ölçüm:** `pip install -e ".[stt,stt-cuda]"` ile GPU'da; CPU karşılaştırması için `STT_TIMEOUT_S=30`.

---

## STT — faster-whisper medium, GPU ölçümü (26 Ekim 2026)

**Ölçen:** Antigravity (Tuğba'nın bilgisayarında). **Doğrulama:** özet satırları ham JSON'lardaki olaylarla tutarlı
(36 akış, 36 FINAL, 0 kare kaybı, 0 `not_recognized`); servis logundaki çözüm süreleri raporla aynı. Model dosyaları
`indir.py --dogrula` ile katalogdaki SHA256'lara karşı doğrulandı.

| | |
|---|---|
| Commit | `10da739` (`.[stt,stt-cuda]`, CUDA kütüphaneleri pip'ten yüklendi) |
| Donanım | RTX 3050 Ti Laptop 4 GB, i5-11400H, Windows; Ollama kapalı |
| Model | faster-whisper-medium `08e178d`, GPU float16 / CPU int8; beam 2; cümle sonu sessizliği 700 ms |
| Set | Piper ile seslendirilmiş 12 asistan cümlesi → 8 kHz G.711 A-law, gerçek zaman hızında (**sentetik ses, iyimser**) |

| Koşu | WER | Ad/birim tam doğru | Konuşma sonu → FINAL p50 / en çok | Whisper çözümü (log) |
|---|---|---|---|---|
| GPU, ipucusuz | %14,0 | 11/12 | 1,06 / 1,17 sn | 0,49–0,66 sn |
| GPU, 11 rehber ipucu | **%10,0** | **12/12** | 1,10 / 1,23 sn | 0,50–0,75 sn |
| CPU int8, ipuçlu (`STT_TIMEOUT_S=30`) | %10,0 | 12/12 | 5,67 / 6,06 sn | 4,97–5,57 sn |

- **Rehber ipucu işe yarıyor:** ipucusuz "Can Öztürk'e" → "canos ürke'yi" (güven 0,64); ipucuyla doğru (0,90). Arama
  ipucusuz hâli de buluyor ama `single_low` (skor 70) → arayana sorardı; ipucuyla net eşleşme.
- **Gecikme:** GPU'da konuşma bitiminden metne ~1,1 sn (≈ cümle sonu bekleme + 0,5–0,75 sn çözüm). İpucu çözümü
  ölçülebilir yavaşlatmıyor.
- **CPU gerçek zamanlı değil:** ~5 sn çözüm. Üretimde STT GPU'da çalışmalı (ADR-010 doğrulandı); CPU yalnızca yedek.
- **GPU belleği:** Whisper medium float16 ölçüm sırasında ~2,2 GB. qwen3.5:4b ~3,7 GB → ikisi 4 GB'a sığmaz; sunucu
  için en az 8 GB, birden çok kanal için daha fazlası gerekir (açık konu #3, Aşama 7'de ölçülecek).
- Kalan hatalar ad dışı: "lütfen" → "lütfü" (3/3 koşu), "bağlar mısınız" → "bağlanmasınız", ipucuyla "orada mı" →
  "oradama". Sentetik sesin telaffuzundan olabilir; gerçek kayıtlarla bakılacak. Hatalı çıktılarda güven düşük
  (0,64–0,68).

**Sınırlar:** n = 12, tek konuşmacı, sentetik (temiz) ses, gürültü yok. NFR-03 (ad/birim tanıma ≥ %90) için gerçek
telefon kayıtları gerekiyor (açık konu #8).

---

## Uçtan uca çağrı — ilk ölçüm, santral simülatörüyle (27 Ekim 2026)

**Ölçen:** Antigravity (Tuğba'nın bilgisayarında). **Doğrulama:** simülatör çıktıları, Gateway `tur:` / santral eylemi
satırları ve STT `FINAL` satırları birbiriyle tutarlı; tur gecikmesi bileşenlerin toplamına uyuyor (aşağıda).

| | |
|---|---|
| Commit | `573d529` (ölçüm anında `f1e94f2`; geçmiş temizliğinden önceki kimlik, kod aynı) |
| Donanım | RTX 3050 Ti 4 GB (ölçüm sırasında **3,9 / 4,0 GB dolu**), i5-11400H, Windows |
| Bileşenler | STT: Whisper medium GPU float16 · LLM: qwen3.5:4b (%52 CPU / %48 GPU), `LLM_TIMEOUT_S=15` · TTS: Piper `tr_TR-fahrettin-medium` (yalnızca iç test, ADR-013) |
| Yol | `tools/cagri/cagri.py` → REST + RTP (G.711 A-law) → Gateway → gRPC STT → LLM + rehber → Piper → RTP; santral eylemleri simülatörün HTTP ucuna |
| Set | Önceki STT ölçümünün sentetik cümleleri, 5 çağrı, tek tur |

| Çağrı | Duyulan (FINAL) | Asistan | Eylem | Tur gecikmesi | STT çözümü | Diyalog (LLM) |
|---|---|---|---|---|---|---|
| A | ayşe yılmaz ile görüşmek istiyorum | Sizi Ayşe Yılmaz'a aktarıyorum. | transfer:214 ✅ | 6,39 sn | 1,66 sn | 3,89 sn |
| B | satış müdürünü arıyorum | Sizi Mehmet Demir'e aktarıyorum. | transfer:230 ✅ | 5,53 sn | 0,93 sn | 3,69 sn |
| C | insan kaynaklarına bağlayın | Sizi Zeynep Arslan'a aktarıyorum. | transfer:260 ✅ | 5,22 sn | 0,97 sn | 3,36 sn |
| D | **orada mı** (beklenen: "zeynep arslan orada mı") | Evet, ben … santralında bulunuyorum. Kiminle görüşmek istersiniz? | — ❌ | 4,67 sn | 0,75 sn | 2,95 sn |
| E | operatöre bağlayın lütfen | Sizi operatöre bağlıyorum, lütfen hatta kalın. | operator ✅ | 3,88 sn | 0,73 sn | 2,33 sn |

- **Zincir çalışıyor:** 5 çağrının 4'ü doğru eylemle bitti; açılış, sağlık, karşılama (ilk ses ≤ 0,016 sn), aktarma
  isteği, `transfer_result`, `DELETE` sözleşmeye uygun. WARNING/ERROR yok (yalnızca API_KEY boş uyarısı).
- **Gecikme hedefin çok üstünde (3,9–6,4 sn; hedef p95 ≤ 2 sn).** Tur gecikmesi ≈ cümle sonu bekleme 0,7 + STT +
  diyalog + TTS ~0,1 (A: 0,7 + 1,66 + 3,89 + 0,1 ≈ 6,35 ✓). **Diyalog (LLM) %60–65.** GPU dolu olduğu için Whisper
  da tek başına ölçümdeki 0,5–0,75 sn yerine 0,73–1,66 sn sürdü. Bu donanım sunucuyu temsil etmez; ama LLM'in
  darboğaz olduğu açık (11.1 iyileştirme seçenekleri).
- **D: "Zeynep Arslan" STT'de kayboldu.** Aynı dosya STT ölçümünde ("zeynep arslan oradama") tanınmıştı. Fark:
  Gateway rehberden 12 ipucu veriyor ve listenin **son** öğesi "Zeynep Arslan". Whisper ipuçlarını "önceki metin"
  olarak alır; ses, istemin son kelimeleriyle başlayınca bunları zaten söylenmiş sayıp atlayabilir. **Varsayım,
  doğrulanacak:** `tools/stt/dene.py --rehber` ile Gateway'in ipuçlarıyla aynı dosyalar tekrar ölçülecek.
  → **Varsayım çürütüldü** (aşağıda "İpucu doğrulaması").
- Busy akışı (D) aktarma olmadığı için denenemedi.

**Sınırlar:** n = 5, tek tur, sentetik ses; GPU paylaşımlı ve dolu.

---

## STT — Gateway ipuçlarıyla doğrulama (27 Ekim 2026)

**Ölçen:** Antigravity. **Doğrulama:** özet satırları ham JSON'larla tutarlı; R ve R2 birebir aynı (deterministik).

| | |
|---|---|
| Commit | `a61e952` |
| Ortam | Yalnızca STT (GPU, float16), Ollama kapalı |
| Koşular | R ve R2: `--rehber data/rehber_ornek.json` (Gateway ile aynı 12 ipucu, son öğe "Zeynep Arslan") · N: ipucusuz |

| Koşu | WER | Ad/birim | 5.wav ("Zeynep Arslan orada mı?") | Konuşma sonu → FINAL p50 |
|---|---|---|---|---|
| R | **%8,0** | 12/12 | zeynep arslan orada | 1,10 sn |
| R2 | %8,0 | 12/12 | zeynep arslan orada | 1,10 sn |
| N | %14,0 | 11/12 ("canos ürke") | zeynep arslan orada mı | 1,10 sn |

- **Varsayım çürütüldü:** ipuçları "Zeynep Arslan"ı yutmuyor; Gateway'in ipucu listesiyle WER en iyi sonuç (%8).
  Yan etki: 5.wav'da soru eki "mı" düştü (aramayı etkilemez).
- **Uçtan uca D'deki kayıp hâlâ açıklanmadı.** Antigravity'nin önerdiği "karşılama sırasında başı kesildi" açıklaması
  sayılarla uyuşmuyor: D'de Whisper'a giden cümle 1,58 sn, burada 1,68 sn — yalnızca ~0,1 sn kısa, oysa "Zeynep Arslan"
  ~0,9 sn. Fark tek koşuda görüldü (n = 1); o ölçümde GPU dolu ve Ollama ile paylaşımlıydı. → Teşhis için STT'ye
  cümle sesi kaydı eklendi (`STT_DEBUG_DIR`): Whisper'a giden ses ve sonuç dosyaya yazılır; tekrar görülürse ses
  dinlenip karşılaştırılacak.

---

## Yük testi — dizüstü, hızlı yol açık (27 Ekim 2026)

**Ölçen:** Antigravity. **Araç:** `tools/yuk/yuk.py` (her çağrı santral gibi: REST + kesintisiz RTP, aktarmaya
`answered`). Arayan sesi: Piper ile üretilmiş "Ayşe Yılmaz ile görüşmek istiyorum." (temiz ses; gerçek hat daha gürültülü).

| | |
|---|---|
| Commit | `6ac445b` |
| Donanım | Dizüstü, RTX 3050 Laptop 4 GB (yük sırasında 3776 / 4096 MiB dolu: Whisper medium + qwen3.5:4b aynı GPU'da) |
| Servisler | STT (GPU), Ollama qwen3.5:4b, Piper, Gateway (`LLM_TIMEOUT_S=15`, hızlı yol açık) |

| Eşzamanlı | Çağrı | Başarılı / reddedilen / hata | Aktarma | Tur p50 | p90 | **p95** | En çok | Karşılama p50 |
|---|---|---|---|---|---|---|---|---|
| 1 | 6 | 6 / 0 / 0 | 5 | 1,50 sn | 2,69 | 3,26 | 3,83 | 0,007 sn |
| 3 | 12 | 12 / 0 / 0 | 12 | 1,47 sn | 1,85 | **2,05** | 2,25 | 0,015 sn |
| 5 | 20 | 20 / 0 / 0 | 20 | 1,51 sn | 2,29 | **2,66** | 3,01 | 0,007 sn |

- **Tur gecikmesi yarıdan aza indi:** ilk uçtan uca ölçümde (24 satır yukarıda) tur 3,9–6,4 sn idi; şimdi p50 ≈ 1,5 sn.
  Neden hızlı yol (ADR-016): net tam ad söylenince LLM hiç çağrılmıyor; kalan süre cümle sonu bekleme (0,7 sn) + STT
  (~0,6 sn) + TTS. p50 eşzamanlılıkla değişmiyor: darboğaz kuyruk değil.
- **Kuyruk (p95) eşzamanlılıkla artıyor:** 3 çağrıda 2,05 sn (NFR-01 sınırı 2 sn), 5 çağrıda 2,66 sn. Aynı GPU'daki
  Whisper çözümleri sıraya giriyor (`STT_NUM_WORKERS=1`). 1 eşzamanlıdaki yüksek p95 tek örnekten (6 tur; bir çağrı
  aktarmadı, büyük olasılıkla o tur LLM'e gitti) — istatistik olarak anlamlı değil.
- **Bu dizüstünün kapasitesi ≈ 3 eşzamanlı çağrı** (p95 ≈ 2 sn). Sunucu kapasitesi kendi GPU'sunda aynı araçla
  ölçülür (tasarim.md 11.3); `MAX_CONCURRENT_CALLS` bulunan değerin %80'i.
- Hiç reddedilen, hatalı ya da cevapsız tur yok; karşılama hazır sesten çalındığı için ~10 ms.

**Aynı raporda bulunan iki sorun (düzeltildi):**
1. Ayar ekranından eklenen "Ayşe Abla" takma adı işe yaramadı: arama anahtarı hitapları ("abla", "hanım") attığı
   için "ayse"ye indi ve iki Ayşe'yle çakıştı ("Hangi Ayşe?"). → Ayar ekranında eklenen takma adlar artık **tam
   hâliyle** (hitabıyla) de eşleşiyor: "ayşe abla (lütfen)" yalnızca Ayşe Yılmaz; hızlı yolda LLM'siz aktarılıyor.
   Rehber kaynağındaki genel hitaplı takma adlar ("Ayşe Hanım") bulanık aramada kalıyor (aynı adlı başkası da
   öyle aranabilir).
2. Windows'ta `bash` WSL yönlendiricisi olduğu için kurulum betiği sözdizimi testi `C:\` yolunu bulamadı → Windows'ta
   atlanıyor (betikler Linux içindir; CI'da Linux'ta çalışıyor).
