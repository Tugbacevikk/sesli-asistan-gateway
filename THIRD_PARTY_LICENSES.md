# Üçüncü taraf bileşenler ve lisansları

Bu dosya `python tools/kurulum/lisanslar.py` ile `deploy/kilit/*.txt`'deki sabit sürümlerden üretilir;
elle değiştirmeyin. Lisans metinleri dağıtım paketinde `lisanslar/` klasöründedir (kurulu paketlerin
`*.dist-info/licenses` dosyaları). ⚠ işaretliler dağıtımdan önce hukuk incelemesi gerektirir.

## Modeller ve ayrı servisler

| Bileşen | Rol | Lisans | Kaynak |
|---|---|---|---|
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
| annotated-types | 0.8.0 | MIT | Gateway, STT, TTS (Seslen), stt-gpu |
| anyio | 4.15.1 | MIT | Gateway, STT, TTS (Seslen), araclar, stt-gpu |
| av | 19.0.2 | BSD-3-Clause | STT, stt-gpu |
| certifi | 2026.10.23 | Mozilla Public License 2.0 (MPL 2.0) | Gateway |
| cffi | 2.1.1 | MIT-0 | araclar |
| click | 8.5.0 | BSD-3-Clause | Gateway, STT, TTS (Seslen), araclar, stt-gpu |
| ctranslate2 | 4.8.4 | MIT | STT, stt-gpu |
| fastapi | 0.162.0 | MIT | Gateway, TTS (Seslen) |
| faster-whisper | 1.2.1 | MIT License | STT, stt-gpu |
| filelock | 4.2.4 | MIT | STT, TTS (Seslen), araclar, stt-gpu |
| flatbuffers | 25.12.19 | Apache Software License | STT, TTS (Seslen), stt-gpu |
| fsspec | 2026.9.0 | BSD-3-Clause | STT, TTS (Seslen), araclar, stt-gpu |
| grpcio | 1.85.0 | Apache-2.0 | Gateway, STT, stt-gpu |
| grpcio-health-checking | 1.85.0 | Apache-2.0 | STT, stt-gpu |
| h11 | 0.16.0 | MIT License | Gateway, STT, TTS (Seslen), araclar, stt-gpu |
| hf-xet | 1.7.0 | Apache-2.0 | STT, TTS (Seslen), araclar, stt-gpu |
| httpcore | 1.0.9 | BSD-3-Clause | Gateway |
| httpcore2 | 2.16.0 | BSD-3-Clause | STT, TTS (Seslen), araclar, stt-gpu |
| httpx | 0.28.1 | BSD License | Gateway |
| httpx2 | 2.16.0 | BSD-3-Clause | STT, TTS (Seslen), araclar, stt-gpu |
| huggingface-hub | 2.3.0 | Apache Software License | STT, TTS (Seslen), araclar, stt-gpu |
| idna | 3.20 | BSD-3-Clause | Gateway, STT, TTS (Seslen), araclar, stt-gpu |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | Gateway, STT, TTS (Seslen), araclar, stt-gpu |
| nvidia-cublas-cu12 | 12.9.2.10 | ⚠ LicenseRef-NVIDIA-Proprietary | stt-gpu |
| nvidia-cuda-nvrtc-cu12 | 12.9.86 | ⚠ Other/Proprietary License | stt-gpu |
| nvidia-cudnn-cu12 | 9.27.0.42 | ⚠ bilinmiyor (PyPI üst verisinde yok; paketin LICENSE dosyasına bakın) | stt-gpu |
| onnxruntime | 1.31.1 | MIT License | STT, TTS (Seslen), stt-gpu |
| opentelemetry-api | 1.45.1 | Apache-2.0 | Gateway, TTS (Seslen) |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | STT, TTS (Seslen), araclar, stt-gpu |
| prometheus-client | 0.26.0 | Apache-2.0 AND BSD-2-Clause | Gateway, STT, stt-gpu |
| protobuf | 7.36.2 | 3-Clause BSD License | Gateway, STT, TTS (Seslen), stt-gpu |
| pycparser | 3.12 | BSD-3-Clause | araclar |
| pydantic | 2.14.1 | MIT | Gateway, STT, TTS (Seslen), stt-gpu |
| pydantic-core | 2.50.1 | MIT | Gateway, STT, TTS (Seslen), stt-gpu |
| pydantic-settings | 2.16.1 | MIT | Gateway, STT, TTS (Seslen), stt-gpu |
| python-dotenv | 1.2.4 | BSD-3-Clause | Gateway, STT, TTS (Seslen), stt-gpu |
| pyyaml | 6.0.3 | MIT License | STT, TTS (Seslen), araclar, stt-gpu |
| rapidfuzz | 3.14.6 | MIT | Gateway |
| scipy | 1.18.1 | BSD License | Gateway, STT, TTS (Seslen), araclar, stt-gpu |
| seslen-tts | 0.1.0 | MIT | TTS (Seslen) |
| sherpa-onnx | 2.0.1 | Apache licensed, as found in the LICENSE file | STT, stt-gpu |
| sherpa-onnx-core | 2.0.1 | Apache-2.0 | STT, stt-gpu |
| soundfile | 0.14.0 | BSD License | araclar |
| starlette | 1.10.0 | BSD-3-Clause | Gateway, TTS (Seslen) |
| tokenizers | 0.23.3 | Apache Software License | STT, stt-gpu |
| tqdm | 4.70.1 | MPL-2.0 AND MIT | STT, TTS (Seslen), araclar, stt-gpu |
| truststore | 0.10.5 | MIT | STT, TTS (Seslen), araclar, stt-gpu |
| turkce-normalize | 0.1.1 | MIT | TTS (Seslen) |
| turkce-vurgu | 0.1.1 | MIT AND CC-BY-SA-4.0 | TTS (Seslen) |
| typing-extensions | 4.16.0 | PSF-2.0 | Gateway, STT, TTS (Seslen), araclar, stt-gpu |
| typing-inspection | 0.4.4 | MIT | Gateway, STT, TTS (Seslen), stt-gpu |
| uvicorn | 0.54.0 | BSD-3-Clause | Gateway, TTS (Seslen) |
| websockets | 17.3 | BSD-3-Clause | Gateway |

İnceleme gereken paketler: nvidia-cublas-cu12, nvidia-cuda-nvrtc-cu12, nvidia-cudnn-cu12.
