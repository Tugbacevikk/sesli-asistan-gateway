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
| Commit | `17b354a` |
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
