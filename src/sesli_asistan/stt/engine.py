"""Türkçe konuşmadan metne: faster-whisper (CTranslate2), varsayılan medium.

Kaynak: Vietnamese-speech-to-text-models `infrastructure/faster_whisper_asr.py`
  - `resolve_device()`                 : aynen (CUDA yoksa CPU'ya düşme, float16 → int8)
  - uydurma önleyici ayarlar           : aynen (WhisperParams; gerekçeler yanında)
  - `transcribe_text()`                : tek cümle çözümü → burada `transcribe()`
  - yükleme hatasında CPU'ya düşme     : aynen
Uyarlanan:
  - `hotwords` çağrı başına verilir (rehber adları akışın StreamConfig'inden gelir).
  - beam varsayılanı 2: Türkçe 8 kHz ölçümünde beam 5 kadar doğru, %20–30 hızlı (tasarim.md 5.2.1).
  - Güven değeri (`confidence`) segmentlerin ortalama log-olasılığından üretilir.
  - Eşzamanlılık: model nesnesi başına `num_workers` kadar çözüm (kaynakta tek kilit vardı;
    o kilit Windows'taki CTranslate2 çökmesi içindi, hedef Ubuntu).
Alınmayan: toplu iş yolu (BatchedInferencePipeline, pack_clips, kelime zamanları, güvenlik ağı
`rescue`), Windows CUDA DLL arama kodu, dil tespiti.
"""
from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from sesli_asistan.libs.text.normalizer import TrNormalizer

from .hallucination import HallucinationFilter

log = logging.getLogger(__name__)
SR = 16000


@dataclass(frozen=True)
class WhisperParams:
    """faster-whisper ayarları. Değerler kaynak sistemin Türkçe ayarlarıdır; farklı olanlar belirtildi."""

    model_dir: str = "models/faster-whisper-medium"
    device: str = "auto"              # "auto" (GPU varsa cuda), "cuda", "cpu"
    compute_type: str = "auto"        # "auto" (cuda'da float16, cpu'da int8)
    device_index: int = 0
    beam_size: int = 2                # kaynakta 5; asistan için 2 (ölçüm: eşit doğruluk, daha hızlı)
    repetition_penalty: float = 1.2
    no_repeat_ngram_size: int = 3
    no_speech_threshold: float = 0.6
    compression_ratio_threshold: float = 2.4
    # None iken "sessizlik olasılığı > 0,6" tek başına parçayı atar; -1,0 ile model metinden
    # eminse parça korunur → sessiz konuşanın cümlesi atılmaz.
    log_prob_threshold: float | None = -1.0
    # Whisper istemi sessiz yerde aynen yazabiliyor; dil verildiği için varsayılan kapalı.
    initial_prompt: str | None = None
    max_repeat: int = 2
    num_workers: int = 1              # aynı modelle eşzamanlı çözüm sayısı
    cpu_threads: int = 4


@dataclass(frozen=True)
class SttResult:
    text: str
    confidence: float                 # 0..1 (segmentlerin ortalama olasılığı)


def resolve_device(dev: str, c_type: str, cuda_ok: bool) -> tuple[str, str]:
    """İstenen cihaz ve hesap türünden gerçekte kullanılacak ikiliyi bulur.

    CUDA istenip bulunamazsa CPU'ya düşülür; GPU'ya özgü float16 türleri CPU'da çalışmaz
    (ValueError: Requested float16 compute type...), bu yüzden int8'e çevrilir."""
    if dev == "auto":
        dev = "cuda" if cuda_ok else "cpu"
    elif dev == "cuda" and not cuda_ok:
        log.warning("CUDA istendi ama CUDA bulunamadı; CPU moduna düşülüyor.")
        dev = "cpu"
    if c_type == "auto":
        c_type = "float16" if dev == "cuda" else "int8"
    elif dev == "cpu" and "float16" in c_type:
        log.warning("compute_type=%s CPU'da çalışmaz; int8 kullanılıyor.", c_type)
        c_type = "int8"
    return dev, c_type


def _cuda_ok() -> bool:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception as e:  # ctranslate2 yok / CUDA yok
        log.debug("CUDA kullanılamıyor: %s", e)
        return False


class WhisperEngine:
    def __init__(self, params: WhisperParams | None = None, model=None,
                 hallucinations: HallucinationFilter | None = None):
        """model: testler için sahte nesne verilebilir; verilmezse diskten yüklenir."""
        self.p = params or WhisperParams()
        self.filter = hallucinations or HallucinationFilter(max_repeat=self.p.max_repeat)
        if self.p.initial_prompt:
            self.filter.add(self.p.initial_prompt)
        self.normalizer = TrNormalizer(lowercase=True, strip_punct=True, latin_only=True)
        self.model = model if model is not None else self._load()
        self.device = str(getattr(getattr(self.model, "model", None), "device", "cpu"))
        self._slots = threading.Semaphore(max(1, self.p.num_workers))

    def _load(self):
        from faster_whisper import WhisperModel

        folder = Path(self.p.model_dir)
        if not (folder / "model.bin").exists():
            # klasör yoksa kütüphane Hugging Face'te arar: internetsiz sunucuda anlaşılmaz hata
            # yerine ne yapılacağı söylenir
            raise FileNotFoundError(f"{folder}/model.bin yok: python tools/models/indir.py")
        cuda_ok = self.p.device != "cpu" and _cuda_ok()
        dev, c_type = resolve_device(self.p.device, self.p.compute_type, cuda_ok)
        log.info("Whisper yükleniyor: %s, device=%s, compute_type=%s", folder, dev, c_type)
        kw = dict(device_index=self.p.device_index, cpu_threads=self.p.cpu_threads,
                  local_files_only=True, num_workers=self.p.num_workers)
        try:
            return WhisperModel(str(folder), device=dev, compute_type=c_type, **kw)
        except Exception as e:
            if dev != "cuda":
                raise
            log.warning("CUDA yükleme hatası (%s), CPU moduna düşülüyor.", e)
            return WhisperModel(str(folder), device="cpu", compute_type="int8", **kw)

    def warmup(self) -> None:
        """İlk çözüm yavaştır (kaynak ölçüm: ilk parça 27,5 sn). Servis hazır demeden önce çağrılır."""
        self.transcribe(np.zeros(SR, np.float32))

    def transcribe(self, samples: np.ndarray, hotwords: list[str] | None = None) -> SttResult:
        """Tek cümlenin sesi (16 kHz float32, yalnızca VAD'ın konuşma bulduğu parça) → metin."""
        if samples.size == 0:
            return SttResult("", 0.0)
        p = self.p
        with self._slots:
            segments, _ = self.model.transcribe(
                samples, language="tr", task="transcribe", vad_filter=False,
                beam_size=p.beam_size, temperature=(0.0, 0.2, 0.4),
                initial_prompt=p.initial_prompt,
                hotwords=" ".join(hotwords) if hotwords else None,
                condition_on_previous_text=False, without_timestamps=True,
                repetition_penalty=p.repetition_penalty, no_repeat_ngram_size=p.no_repeat_ngram_size,
                no_speech_threshold=p.no_speech_threshold,
                compression_ratio_threshold=p.compression_ratio_threshold,
                log_prob_threshold=p.log_prob_threshold)
            segments = list(segments)  # sonuçlar tembel üretilir; hatalar burada çıksın
        parts, logps = [], []
        for s in segments:
            if self.filter.is_hallucination(s.text):
                continue
            text = self.normalizer.normalize(s.text)
            if text:
                parts.append(text)
                logps.append(getattr(s, "avg_logprob", 0.0))
        words = self.filter.drop_repeats(" ".join(parts).split())
        conf = math.exp(sum(logps) / len(logps)) if logps else 0.0
        return SttResult(" ".join(words), round(min(1.0, conf), 3))
