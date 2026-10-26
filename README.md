# Sesli Asistan Gateway

Santrale gelen çağrıyı Türkçe karşılayan, arayanın söylediği kişi veya birimi şirket rehberinde
bulan ve çağrıyı ilgili dahiliye aktaran sesli asistan.

```
Santral ──RTP (G.711)──► Gateway ──gRPC──► STT (Silero VAD + Whisper medium)
   ▲                        │  └──HTTP──► LLM (Ollama, qwen3.5:9b) + InContacts / TransferCall
   └──RTP + REST (aktar)────┘  └──HTTP──► TTS (Piper)
```

Mimari, arayüz sözleşmeleri, kararlar (ADR) ve yol haritası: **[docs/tasarim.md](docs/tasarim.md)**.
Ölçüm kayıtları: **[docs/olcumler.md](docs/olcumler.md)**.

## Durum

| Aşama | İçerik | Durum |
|---|---|---|
| 1 | Repo iskeleti, proto sözleşmeleri, katman kuralları | ✅ |
| — | Mevcut STT sisteminden taşınan modüller (tasarim.md Ek D) | ✅ |
| 2 | Diyalog: LLM + InContacts / TransferCall / ConnectOperator, aktarma güvenlik kuralı, Türkçe rehber araması, diyalog kuralları, test seti | ✅ (gerçek modelle ölçüm bekliyor) |
| 3 | TTS entegrasyonu | ⏳ |
| 4 | gRPC STT servisi | ⏳ |
| 5 | Santral entegrasyonu (REST kontrol, çağrı oturumu) | ⏳ |
| 6–10 | Dayanıklılık, performans, kurulum, rehber API, kabul | ⏳ |

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

## Geliştirme

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,tools,gateway]"  # STT servisi için ayrıca ".[stt]"
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
