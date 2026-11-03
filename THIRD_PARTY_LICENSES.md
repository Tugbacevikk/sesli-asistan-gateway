# Üçüncü taraf bileşenler ve lisansları

Bu dosya `python tools/kurulum/lisanslar.py` ile `deploy/kilit/*.txt`'deki sabit sürümlerden üretilir;
elle değiştirmeyin. Lisans metinleri dağıtım paketinde `lisanslar/` klasöründedir (kurulu paketlerin
`*.dist-info/licenses` dosyaları). ⚠ işaretliler dağıtımdan önce hukuk incelemesi gerektirir.

## Modeller ve ayrı servisler

| Bileşen | Rol | Lisans | Kaynak |
|---|---|---|---|
| turkish-stt-66m-v2 (mihu) — Powered by mihu | STT modeli (varsayılan) | mihu Community License v1.0 | https://huggingface.co/mihuai/turkish-stt-66m-v2 |
| faster-whisper-medium (Systran, CTranslate2 dönüşümü) | STT modeli | MIT | https://huggingface.co/Systran/faster-whisper-medium |
| Whisper medium ağırlıkları (OpenAI) | STT modeli | MIT | https://github.com/openai/whisper |
| Silero VAD (ONNX) | Konuşma algılama | MIT | https://github.com/snakers4/silero-vad |
| Qwen3.5 4B (Ollama) | Dil modeli | Apache-2.0 (model kartında teyit edilir) | https://ollama.com/library/qwen3.5 |
| Ollama | LLM çalıştırıcı (ayrı servis) | MIT | https://github.com/ollama/ollama |
| Piper (piper1-gpl) + espeak-ng | TTS (ayrı süreç, mevcut servis) | ⚠ GPL-3.0 — yalnızca ayrı süreç (C-05) | https://github.com/OHF-Voice/piper1-gpl |
| Seslen TTS model ağırlıkları ve sesleri | Kadın sesi (ADR-015, isteğe bağlı) | CC BY-SA 4.0 — atıf + aynı lisansla paylaşım; yapay zekâ sesi olduğu belirtilir | https://huggingface.co/seslen-tr |
| NVIDIA cuBLAS / cuDNN (pip) | STT GPU kütüphaneleri | ⚠ NVIDIA Software License — yeniden dağıtım şartları hukukça teyit edilmeli | https://docs.nvidia.com/cuda/eula/ |

## Python paketleri

| Paket | Sürüm | Lisans | Servis |
|---|---|---|---|
| annotated-doc | 0.0.5 | MIT | Gateway, TTS (Seslen) |
| annotated-types | 0.8.0 | MIT | Gateway, STT, TTS (Seslen) |
| anyio | 4.15.1 | MIT | Gateway, Kurulum araçları, STT, TTS (Seslen) |
| av | 19.0.2 | BSD-3-Clause | STT |
| certifi | 2026.10.23 | Mozilla Public License 2.0 (MPL 2.0) | Gateway |
| cffi | 2.1.1 | MIT-0 | Kurulum araçları |
| click | 8.5.0 | BSD-3-Clause | Gateway, Kurulum araçları, STT, TTS (Seslen) |
| ctranslate2 | 4.8.4 | MIT | STT |
| fastapi | 0.162.0 | MIT | Gateway, TTS (Seslen) |
| faster-whisper | 1.2.1 | MIT License | STT |
| filelock | 4.2.4 | MIT | Kurulum araçları, STT, TTS (Seslen) |
| flatbuffers | 25.12.19 | Apache Software License | STT, TTS (Seslen) |
| fsspec | 2026.9.0 | BSD-3-Clause | Kurulum araçları, STT, TTS (Seslen) |
| grpcio | 1.85.0 | Apache-2.0 | Gateway, STT |
| grpcio-health-checking | 1.85.0 | Apache-2.0 | STT |
| h11 | 0.16.0 | MIT License | Gateway, Kurulum araçları, STT, TTS (Seslen) |
| hf-xet | 1.7.0 | Apache-2.0 | Kurulum araçları, STT, TTS (Seslen) |
| httpcore | 1.0.9 | BSD-3-Clause | Gateway |
| httpcore2 | 2.16.0 | BSD-3-Clause | Kurulum araçları, STT, TTS (Seslen) |
| httpx | 0.28.1 | BSD License | Gateway |
| httpx2 | 2.16.0 | BSD-3-Clause | Kurulum araçları, STT, TTS (Seslen) |
| huggingface-hub | 2.3.0 | Apache Software License | Kurulum araçları, STT, TTS (Seslen) |
| idna | 3.20 | BSD-3-Clause | Gateway, Kurulum araçları, STT, TTS (Seslen) |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | Gateway, Kurulum araçları, STT, TTS (Seslen) |
| nvidia-cublas-cu12 | 12.9.2.10 | ⚠ LicenseRef-NVIDIA-Proprietary | STT |
| nvidia-cuda-nvrtc-cu12 | 12.9.86 | ⚠ Other/Proprietary License | STT |
| nvidia-cudnn-cu12 | 9.27.0.42 | ⚠ bilinmiyor (PyPI üst verisinde yok; paketin LICENSE dosyasına bakın) | STT |
| onnxruntime | 1.31.1 | MIT License | STT, TTS (Seslen) |
| opentelemetry-api | 1.45.1 | Apache-2.0 | Gateway, TTS (Seslen) |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | Kurulum araçları, STT, TTS (Seslen) |
| prometheus-client | 0.26.0 | Apache-2.0 AND BSD-2-Clause | Gateway, STT |
| protobuf | 7.36.2 | 3-Clause BSD License | Gateway, STT, TTS (Seslen) |
| pycparser | 3.12 | BSD-3-Clause | Kurulum araçları |
| pydantic | 2.14.1 | MIT | Gateway, STT, TTS (Seslen) |
| pydantic-core | 2.50.1 | MIT | Gateway, STT, TTS (Seslen) |
| pydantic-settings | 2.16.1 | MIT | Gateway, STT, TTS (Seslen) |
| python-dotenv | 1.2.4 | BSD-3-Clause | Gateway, STT, TTS (Seslen) |
| pyyaml | 6.0.3 | MIT License | Kurulum araçları, STT, TTS (Seslen) |
| rapidfuzz | 3.14.6 | MIT | Gateway |
| scipy | 1.18.1 | BSD License | Gateway, Kurulum araçları, STT, TTS (Seslen) |
| seslen-tts | 0.1.0 | MIT | TTS (Seslen) |
| sherpa-onnx | 2.0.1 | Apache licensed, as found in the LICENSE file | STT |
| sherpa-onnx-core | 2.0.1 | Apache-2.0 | STT |
| soundfile | 0.14.0 | BSD License | Kurulum araçları |
| starlette | 1.10.0 | BSD-3-Clause | Gateway, TTS (Seslen) |
| tokenizers | 0.23.3 | Apache Software License | STT |
| tqdm | 4.70.1 | MPL-2.0 AND MIT | Kurulum araçları, STT, TTS (Seslen) |
| truststore | 0.10.5 | MIT | Kurulum araçları, STT, TTS (Seslen) |
| turkce-normalize | 0.1.1 | MIT | TTS (Seslen) |
| turkce-vurgu | 0.1.1 | MIT AND CC-BY-SA-4.0 | TTS (Seslen) |
| typing-extensions | 4.16.0 | PSF-2.0 | Gateway, Kurulum araçları, STT, TTS (Seslen) |
| typing-inspection | 0.4.4 | MIT | Gateway, STT, TTS (Seslen) |
| uvicorn | 0.54.0 | BSD-3-Clause | Gateway, TTS (Seslen) |
| websockets | 17.3 | BSD-3-Clause | Gateway |

İnceleme gereken paketler: nvidia-cublas-cu12, nvidia-cuda-nvrtc-cu12, nvidia-cudnn-cu12.
