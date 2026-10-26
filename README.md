# Sesli Asistan Gateway

Santrale gelen çağrıyı Türkçe karşılayan, arayanın söylediği kişi veya birimi şirket rehberinde
bulan ve çağrıyı ilgili dahiliye aktaran sesli asistan.

```
Santral ──RTP (G.711)──► Gateway ──gRPC──► STT (Silero VAD + Whisper medium)
   ▲                        │  └──HTTP──► LLM (Ollama, qwen3.5:9b) + InContacts / TransferCall
   └──RTP + REST (aktar)────┘  └──HTTP──► TTS (Piper)
```

Mimari, arayüz sözleşmeleri, kararlar (ADR) ve yol haritası: **[docs/tasarim.md](docs/tasarim.md)**.

## Durum

| Aşama | İçerik | Durum |
|---|---|---|
| 1 | Repo iskeleti, proto sözleşmeleri, katman kuralları | ✅ |
| — | Mevcut STT sisteminden taşınan modüller (tasarim.md Ek D) | ✅ |
| 2 | LLM + InContacts / TransferCall | ⏳ |
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

## Geliştirme

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,tools]"          # + ".[stt]" ya da ".[gateway]"
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
