# Sesli Asistan Gateway

Santrale gelen çağrıyı Türkçe karşılayan, arayanın söylediği kişi veya birimi şirket rehberinde
bulan ve çağrıyı ilgili dahiliye aktaran sesli asistan.

```
Santral ──RTP (G.711 / G.722)──► Gateway ──gRPC──► STT (Silero VAD + mihu akışlı model; GPU'da Whisper)
   ▲                                │  ├──MCP──► yerel rehber sunucusu (konu adımı)
   │                                │  ├──HTTP─► LLM (Ollama, qwen3.5) + InContacts / TransferCall
   └──RTP + REST (aktar)────────────┘  └──HTTP─► TTS (Piper)
```

## Kod haritası

Bir çağrının yolu, baştan sona (`src/sesli_asistan/`):

1. **Santral → Gateway:** `gateway/api/app.py` (REST: çağrı aç / olay / kapat) → `core/manager.py` (çağrı ve port
   havuzu) → `core/call.py` (`CallSession`: karşılama, dinleme, söze girme, sessizlik, aktarma; ses
   `adapters/rtp/`). Tarayıcıdan deneme aynı oturumu kullanır (`api/ui.py`).
2. **Ses → metin:** `adapters/stt/` (gRPC istemcisi) → ayrı servis `stt/` (`service.py`: akış, `vad.py`: cümle
   sınırı, `sherpa_engine.py` / `engine.py`: tanıma). Arka plan sesi ve söze girme: `core/level.py`.
3. **Metin → karar:** `core/dialog/` (`DialogAgent.handle_user`). Söz sırayla `steps.py`'deki adımlardan
   (`STEPS`) geçer; ilk cevap veren turu bitirir:
   - `rules.py`: hızlı yol (tam ad, birim, dahili), unvan ve kişi çözme, konu adımı (MCP)
   - `choice.py`: birden çok aday → "hangisi?" (birim yoluyla seviye seviye) ve cevaptan seçim
   - `transfer.py`: aktarmadan önceki denetimler (teyit, aktarılamaz makam, belgegeçer), operatör, deneme sayacı
   - `llm.py`: kurallar çözemezse LLM döngüsü (araçlar: `core/tools.py`)
4. **Rehber:** `core/search.py` (bulanık arama, Türkçe ekler), `core/people.py` (unvan / birim / ad kuralları),
   `core/units.py` (birim yolu), `core/directory.py` (önbellek, yenileme). Kaynaklar: `adapters/directory/`;
   dosyayla yükleme `directory_import.py` + `abbreviations.py` (kısaltma açma).
5. **Aktarma güvenliği:** `core/policy.py` (yalnız rehberde aktarılabilir dahili) ← `core/tools.py` (TransferCall).
6. **Metin → ses:** `core/speech.py` (cümle cümle seslendirme, sabit anonslar) → `adapters/tts/` → servis `tts/`.

Kurulum ve bağlama: `gateway/composition.py` (ayardan nesneler), `config.py` (bütün ayarlar). Kuruma özgü ayarlar
kodda değil dosyalarda: rehber, kısaltmalar ve STT düzeltmeleri (`ABBREVIATIONS_PATH`), konular (`MCP_TOPICS_PATH`).
Davranışın ölçüsü: `tests/data/diyalog_seti*.yaml` (CI'da kural modunda koşar, `tests/unit/test_diyalog_setleri.py`).

Mimari, arayüz sözleşmeleri, kararlar (ADR) ve yol haritası: **[docs/tasarim.md](docs/tasarim.md)**.
Ölçüm kayıtları: **[docs/olcumler.md](docs/olcumler.md)**.

## Durum

| Aşama | İçerik | Durum |
|---|---|---|
| 1 | Repo iskeleti, proto sözleşmeleri, katman kuralları | ✅ |
| — | Mevcut STT sisteminden taşınan modüller (tasarim.md Ek D) | ✅ |
| 2 | Diyalog: LLM + InContacts / TransferCall / ConnectOperator, aktarma güvenlik kuralı, Türkçe rehber araması, diyalog kuralları, test seti | ✅ (qwen3.5:4b ile 90/90, olcumler.md) |
| 3 | TTS: cümle cümle boru hattı, sabit anons kütüphanesi, önbellek, Piper HTTP adaptörü | ✅ (gerçek Piper ile ölçüm bekliyor) |
| 4 | gRPC STT servisi (okuyucu/çözücü ayrı, sağlık protokolü, akış sınırı) + Gateway istemcisi | ✅ (GPU: WER %10, ad 12/12, konuşma sonu → metin ~1,1 sn; sentetik set, olcumler.md) |
| 5 | Santral entegrasyonu: REST kontrol API'si, çağrı oturumu (durum makinesi, barge-in, aktarma akışı, zaman aşımları), RTP medya, santral simülatörü | ✅ (simülatörde; gerçek santral test ortamı bekliyor) |
| — | Arayüz: tarayıcıdan deneme çağrısı + izleme (ADR-014) | ✅ |
| 6 | Dayanıklılık: devre kesici, LLM / MCP / STT düşüşünde kural tabanlı tur, izole hata → operatör | ✅ |
| 7 | Kurulum ve işletim: `install.sh`, paket, yedek / geri yükleme, destek paketi, Prometheus / Grafana izleme | ✅ (konteynerde; GPU sunucuda kabul bekliyor) |
| 8 | Rehber: dosyayla yükleme (kısaltma açma, önizleme / geri alma), yerel MCP rehber sunucusu, makam hatları, birim yapılı rehber (ADR-030, ADR-032) | ✅ (Sentra rehber API'si bekliyor) |
| 9 | Performans | ⏳ gerçek santral ve GPU sunucuda ölçülecek |
| 10 | Kabul: gerçek santral, gerçek rehber, telefon hattından test seti | ⏳ |

### Taşınan modüller

Kaynak: `Tugbacevikk/Vietnamese-speech-to-text-models` (Türkçe yolu test edilmiş sürüm). Her dosyanın
başında neyin aynen alındığı, neyin uyarlandığı ve neyin alınmadığı yazılıdır.

| Modül | Ne yapar |
|---|---|
| `libs/audio/g711.py` | A-law / μ-law çözme + kodlama (numpy) |
| `libs/audio/resample.py` | 8 ↔ 16 kHz; akışta parça sınırında tıkırtısız (scipy, soxr/LGPL yerine) |
| `libs/text/normalizer.py` | Türkçe küçük harf, noktalama, başka alfabeden uydurma kelime süzgeci |
| `gateway/adapters/rtp/` | RTP ayrıştırma/oluşturma, sıra tamponu, çağrı başına alıcı (soket bağımsız boşaltılır), 20 ms tempolu gönderici (barge-in'de iptal) |
| `stt/vad.py` | Silero VAD (ONNX) + cümle bölücü: `SpeechStarted`, `Utterance` |
| `stt/engine.py` | faster-whisper medium, rehber `hotwords`, uydurma önleyici ayarlar, güven değeri |
| `stt/hallucination.py` | Gözlenmiş Türkçe uydurma cümleleri, tekrar süzme |
| `tools/rtp_simulator/` | Santral yerine WAV → RTP (kayıp/sıra bozma), cevabı kaydeder |
| `tools/models/indir.py` | `deploy/models.yaml`'a göre indirme + SHA256 doğrulama |
| `tests/eval/wer.py` | Türkçe WER/CER (kaynak sistemle aynı normalizasyon) |

### Diyalog (Aşama 2)

| Modül | Ne yapar |
|---|---|
| `gateway/core/dialog.py` | LLM araç döngüsü; iki deneme kuralı, LLM hatasında operatör, tur ve geçmiş sınırı (kodda) |
| `gateway/core/tools.py` | `InContacts`, `TransferCall`, `ConnectOperator` tanımları ve yürütücüleri |
| `gateway/core/policy.py` | Aktarma yalnızca son rehber sonucundaki, aktarılabilir dahiliye (uydurma / enjeksiyon koruması) |
| `gateway/core/search.py` | Türkçe bulanık arama: ekler, hitaplar, unvanlar, rakamlar; `single_high / single_low / multiple / none` |
| `gateway/core/directory.py` | Rehber önbelleği: artımlı güncelleme (sunucu saatiyle), kaynak düşerse son kopya |
| `gateway/adapters/llm/` | Ollama yerel API (`think: false`), OpenAI uyumlu uç (llama.cpp) |
| `gateway/adapters/directory/json_dir.py` | Sentra API hazır olana kadar aynı biçimde JSON rehber |
| `tools/sohbet/sohbet.py` | Asistanla yazarak konuşma; `/mesgul`, `/cevapsiz`, `/basarili` ile santral olayı |
| `tests/eval/diyalog.py` | 30 senaryoluk setle görev başarısı, izinsiz aktarma, gecikme; 9B / 4B karşılaştırması |

```bash
ollama pull qwen3.5:9b && ollama pull qwen3.5:4b
python tools/sohbet/sohbet.py --ayrinti
python tests/eval/diyalog.py --model qwen3.5:4b --tekrar 3 --zaman-asimi 60   # doğruluk
python tests/eval/diyalog.py --model qwen3.5:4b --tekrar 3                    # gecikme (5 sn sınırı)
```

### TTS (Aşama 3)

| Modül | Ne yapar |
|---|---|
| `gateway/core/speech.py` | Cümle bölme, rakam → yazı, 8 kHz telefon sesi, `Speaker` (boru hattı, barge-in, yedek anons), `PromptLibrary` |
| `gateway/adapters/tts/piper_http.py` | Piper HTTP (`/synthesize`; yol/biçim ayarlanabilir) |
| `gateway/adapters/tts/cache.py` | Bellek + disk önbelleği |
| `tools/tts/seslendir.py` | Piper denemesi, telefonda duyulacak WAV'lar, gecikme ölçümü (`sonuc_tts.json`) |

```bash
python tools/tts/seslendir.py --anonslar --olcum --tekrar 5 --url http://127.0.0.1:5500
```

### STT servisi (Aşama 4)

| Modül | Ne yapar |
|---|---|
| `stt/service.py` | `stt.v1.SpeechToText/Stream`: G.711 → 16 kHz → VAD → Whisper; okuma çözümü beklemez |
| `stt/server.py` | Model yükleme + ısınma, gRPC sağlık protokolü, SIGTERM'de düzgün kapanış |
| `gateway/adapters/stt/grpc_client.py` | Gateway tarafı akış istemcisi (bağlantı zaman aşımı, bir kez yeniden bağlanma) |
| `contracts/` | `proto/`'dan üretilmiş kod (`python tools/proto/uret.py`; CI `--kontrol` ile denetler) |
| `tools/stt/dene.py` | WAV'ı santral gibi gerçek zamanlı gönderir; olaylar, cümle sonu → FINAL gecikmesi, WER |

```bash
python tools/models/indir.py                       # faster-whisper-medium + silero-vad
pip install -e ".[stt,stt-cuda]"                   # GPU: cuBLAS 12 + cuDNN 9 (CUDA Toolkit gerekmez)
python -m sesli_asistan.stt                        # STT servisi (ayrı terminal)
python tools/stt/dene.py kayit.wav --ipucu "Ayşe Yılmaz"
```

### Santral entegrasyonu (Aşama 5)

| Modül | Ne yapar |
|---|---|
| `gateway/api/app.py` | `POST /v1/calls`, `/events`, `DELETE`, `/v1/health`; Bearer anahtar + IP izin listesi; ortak hata gövdesi |
| `gateway/core/manager.py` | Port havuzu, eşzamanlı çağrı sınırı, hazır değilken ret (503), idempotent bitirme |
| `gateway/core/call.py` | Çağrı durum makinesi: karşılama → dinleme → düşünme → konuşma → aktarma; barge-in, aktarma sonucu, DTMF, zaman aşımları, zarif bozulma |
| `gateway/adapters/rtp/media.py` | Çağrının RTP yolu (giriş + simetrik çıkış) |
| `gateway/adapters/pbx/rest.py` | Gateway → santral eylemleri (`transfer`, `operator`), 3 deneme |
| `gateway/composition.py` | `GatewayRuntime`: açılış (rehber, anonslar, LLM ısınma), arka planda sağlık, kapanış |
| `tools/cagri/cagri.py` | Santral simülatörü: çağrı açar, WAV'ları RTP ile söyler, eylemlere cevap verir; **uçtan uca tur gecikmesi** |
| `tools/yuk/yuk.py` | Yük testi: aynı anda N sanal çağrı; tur gecikmesi p50/p95, reddedilen, en yüksek eşzamanlı (tasarim.md 11.3) |

```bash
python -m sesli_asistan.stt                                   # 1. terminal
PBX_BASE_URL=http://127.0.0.1:8100 python -m sesli_asistan.gateway   # 2. terminal (Ollama + Piper açık)
python tools/cagri/cagri.py ayse.wav --cevap cevap.wav        # 3. terminal: santral gibi ara
```

### Santral denemesi (Asterisk)

`deploy/asterisk/` (deneme santrali ayarları, `kur.sh`) + `tools/santral/asterisk_kopru.py` (ARI köprüsü): softphone'dan
500'ü arayıp gerçek SIP/RTP ile asistanla konuşma, gerçek aktarma (meşgul / cevapsız dahil). Ayrıntı: tasarim.md 15.7.

### Kadın sesi (ADR-015)

`sesli_asistan.tts`: Seslen (açık Türkçe TTS, ONNX, CPU) ile kadın sesi (`ayse`). Piper'la aynı HTTP sözleşmesi;
Gateway'de yalnızca `TTS_URL` değişir. Model ağırlıkları CC BY-SA 4.0 (ticari kullanım serbest, atıf şartı).

```bash
pip install -e ".[tts]"
python -m sesli_asistan.tts                     # port 5600, ilk açılışta model indirilir (~330 MB)
TTS_URL=http://127.0.0.1:5600 python -m sesli_asistan.gateway
```

### Arayüz

Gateway çalışırken **http://127.0.0.1:8000/ui**:

- **Deneme çağrısı:** mikrofona konuşursunuz, asistan sesli cevap verir. Sesiniz telefon kalitesine indirilip santral
  çağrısıyla aynı yoldan geçer. Ekranda duyulan metin, cevap, aktarma isteği ve ilk ses süresi görünür; aktarma
  sonucunu (cevaplandı / meşgul / cevap yok) siz seçersiniz.
- **İzleme:** servis durumu, süren ve son çağrılar (tur tur), rehberde bulunamayan aramalar.

Mikrofon yalnızca `localhost` ya da HTTPS'te açılır. Erişim: `UI_ALLOWED_IPS`, `API_KEY` / `UI_USERS`: ilk açılışta `/ui?anahtar=...`, anahtar oturum çerezine çevrilip adresten silinir.

## Sunucuya kurulum

Ubuntu 24.04 + NVIDIA GPU (ayrıntı: [tasarim.md 14](docs/tasarim.md#14-kurulum-ve-dağıtım)):

```bash
sudo apt install python3.12-venv                 # Ollama: https://ollama.com/download/linux
sudo deploy/install.sh --kontrol                 # ön kontroller
sudo deploy/install.sh                           # kur / güncelle (duman testi geçmezse önceki sürüme döner)
sudo nano /etc/sesli-asistan/gateway.env         # santral adresi, izinli IP'ler, rehber, mesai
sudo systemctl restart sesli-gateway
```

İnternetsiz sunucu: internetli makinede `deploy/paket.sh --imza <anahtar>`, sunucuda
`sudo deploy/install.sh --paket sesli-asistan-<sürüm>.tar.gz --imza-anahtari <açık-anahtar>` (imza doğrulanmadan
kurulmaz; sonraki güncellemeler kurulu sürümün betiğiyle).
Bağımlılıklar `deploy/kilit/`'teki sabit sürüm + SHA256 kilitlerinden kurulur (`tools/kurulum/kilit.sh` üretir);
lisanslar: [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md). Arayüze uzaktan HTTPS: `deploy/nginx/`.

- Rehber: arayüzde **Rehber → Rehber dosyası** ile Excel / CSV yüklenir (önizleme, satır numaralı hatalar, geri alma).
- Sorun bildirimi: `sudo python3 /opt/sesli-asistan/current/tools/kurulum/destek.py` → gizli değerleri maskelenmiş
  tek dosya (sürüm, servisler, sağlık, loglar).
- Kaldırma: `sudo deploy/install.sh --kaldir` (ayarlar ve veriler kalır), `--kaldir --verilerle` (önce son yedek).
- Sürümler ve değişiklikler: [CHANGELOG.md](CHANGELOG.md) (tasarim.md 14.9).

## Geliştirme

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,tools,gateway,stt]"
pytest -q                               # birim testleri (model gerekmez)
ruff check .
lint-imports                            # servis sınırları
```

Modeller:

```bash
python tools/models/indir.py            # faster-whisper-medium + silero-vad → models/
```

Santral olmadan ses denemesi:

```bash
python tools/rtp_simulator/rtp_gonder.py kayit.wav --hedef 127.0.0.1:10000 --cevap cevap.wav
```

## Kurallar

- Yalnızca Türkçe (ADR-006). Dil tespiti yok.
- Üçüncü taraf kütüphaneler repoya kopyalanmaz; modeller repoya girmez.
- GPL lisanslı bileşenler (Piper) yalnızca ayrı süreç olarak kullanılır.
- Her ölçüm `n`, donanım, commit ve ayarlarla birlikte kaydedilir (tasarim.md 15.2).
