"""Konuşma algılama (VAD) ve cümle sonu tespiti.

Kaynak: Vietnamese-speech-to-text-models
  - `infrastructure/live_vad.py` → `EnergyDetector`, `SileroFrameDetector`, `make_detector` (aynen)
  - `application/live.py`        → `Segmenter` (uyarlandı)
Uyarlanan (asistan için):
  - `SpeechStarted` olayı eklendi: konuşma başladığı anda üretilir; Gateway buna göre asistanın
    sesini keser (barge-in, NFR-02).
  - Ara metin (`PartialJob`) çıkarıldı: Whisper akışsız; bu sürümde PARTIAL üretilmez (ADR-010).
  - Cümle sonu sessizliği 700 ms (kaynakta 650); en uzun cümle 12 sn (aynı).
Alınmayan: `ContiguousSegmenter` (Vietnamca Zipformer içindi; Whisper sessizlikte uydurduğu için
Türkçede konuşma parçalarıyla çalışılır).

Silero, PyTorch yerine sherpa-onnx (ONNX) ile çalışır; ~2 GB bağımlılık gerekmez.
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
FRAME_SAMPLES = 512                      # Silero VAD 16 kHz'de tam 512 örnek (32 ms) bekler
FRAME_SEC = FRAME_SAMPLES / SAMPLE_RATE  # 0.032 sn


class SpeechDetector(Protocol):
    """512 örneklik (16 kHz'de 32 ms) çerçevede konuşma var mı?"""

    def __call__(self, frame: np.ndarray) -> bool: ...

    def reset(self) -> None: ...


class EnergyDetector:
    """Uyarlanabilir gürültü tabanlı basit enerji algılayıcı (model gerektirmez; yedek ve testler)."""

    def __init__(self, floor: float = 0.006, multiplier: float = 3.0):
        self.floor = floor
        self.multiplier = multiplier
        self.noise = floor

    def __call__(self, frame: np.ndarray) -> bool:
        rms = float(np.sqrt(np.mean(frame ** 2)) + 1e-12)
        speech = rms > max(self.floor, self.noise * self.multiplier)
        if not speech:
            self.noise = 0.95 * self.noise + 0.05 * rms
        return speech

    def reset(self) -> None:
        self.noise = self.floor


class SileroFrameDetector:
    """Her akışın kendi örneği olur (model durum tutar).

    sherpa-onnx olasılığı doğrudan vermez; dedektörün "şu an konuşma var mı" durumu kullanılır.
    Asıl cümle sonu kararı Segmenter'da; buradaki süreler kısa tutulur ki iki karar birbirine
    eklenip gecikme yaratmasın."""

    def __init__(self, model: str, threshold: float = 0.5):
        import sherpa_onnx

        mc = sherpa_onnx.VadModelConfig()
        mc.silero_vad.model = model
        mc.silero_vad.threshold = threshold
        mc.silero_vad.min_silence_duration = 0.1
        mc.silero_vad.min_speech_duration = 0.1
        mc.silero_vad.max_speech_duration = 3600.0  # bölmeyi Segmenter yapar
        mc.silero_vad.window_size = FRAME_SAMPLES
        mc.sample_rate = SAMPLE_RATE
        mc.num_threads = 1
        self._make = lambda: sherpa_onnx.VoiceActivityDetector(mc, buffer_size_in_seconds=30)
        self.vad = self._make()

    def __call__(self, frame: np.ndarray) -> bool:
        self.vad.accept_waveform(np.ascontiguousarray(frame, dtype=np.float32))
        speech = self.vad.is_speech_detected()
        while not self.vad.empty():  # biten parçalar tutulmaz (bellek büyümesin)
            self.vad.pop()
        return speech

    def reset(self) -> None:
        self.vad = self._make()


def make_detector(kind: str, model: str, threshold: float = 0.5) -> SpeechDetector:
    """kind="silero" ve model dosyası varsa Silero; yoksa uyarıyla enerji algılayıcı."""
    if kind == "silero":
        if Path(model).exists():
            try:
                return SileroFrameDetector(model, threshold)
            except Exception as e:  # pragma: no cover - kurulum sorunu
                log.warning("Silero VAD açılamadı (%s); enerji tabanlı algılayıcı kullanılıyor", e)
        else:
            log.warning("%s yok (tools/models/indir.py); enerji tabanlı algılayıcı kullanılıyor", model)
    return EnergyDetector()


@dataclass
class SegmenterConfig:
    end_silence_ms: int = 700          # bu kadar sessizlik → cümle bitti
    max_utterance_sec: float = 12.0    # durmadan konuşulursa zorla böl
    pre_roll_ms: int = 350             # konuşma başlamadan önceki sesi de al (ilk hece kesilmesin)
    start_frames: int = 3              # konuşma başladı demek için ardışık konuşma çerçevesi (~96 ms)
    min_voiced_sec: float = 0.3        # toplam sesli süre bundan azsa (öksürük, tık) at
    tail_keep_ms: int = 200            # cümle sonundaki sessizlikten bırakılacak pay


@dataclass
class SpeechStarted:
    start: float                       # akış başından itibaren sn


@dataclass
class Utterance:
    audio: np.ndarray                  # 16 kHz float32
    start: float
    end: float


class Segmenter:
    """Sürekli sesi konuşma parçalarına (cümle) böler.

    `feed()` 16 kHz mono float32 örnek alır, ortaya çıkan olayları döner:
      SpeechStarted → konuşma başladı (barge-in)
      Utterance     → cümle bitti, Whisper'a gidecek ses
    """

    def __init__(self, detector: SpeechDetector, config: SegmenterConfig | None = None):
        self.detector = detector
        self.cfg = config or SegmenterConfig()
        self._leftover = np.zeros(0, dtype=np.float32)
        self._pre_roll: deque[np.ndarray] = deque(
            maxlen=max(1, round(self.cfg.pre_roll_ms / 1000 / FRAME_SEC)))
        self._frames: list[np.ndarray] = []
        self._in_speech = False
        self._speech_run = 0
        self._silence_run = 0
        self._voiced = 0
        self._frames_seen = 0
        self._start_frame = 0

    @property
    def stream_time(self) -> float:
        return self._frames_seen * FRAME_SEC

    def _audio(self) -> np.ndarray:
        return np.concatenate(self._frames) if self._frames else np.zeros(0, dtype=np.float32)

    def _close(self, trim: bool) -> Utterance | None:
        audio = self._audio()
        if trim and self._silence_run:
            cut = self._silence_run * FRAME_SAMPLES - int(self.cfg.tail_keep_ms / 1000 * SAMPLE_RATE)
            if cut > 0:
                audio = audio[: len(audio) - cut]
        voiced_sec = self._voiced * FRAME_SEC
        start = self._start_frame * FRAME_SEC

        self._frames = []
        self._in_speech = False
        self._speech_run = self._silence_run = self._voiced = 0

        if voiced_sec < self.cfg.min_voiced_sec:
            return None
        return Utterance(audio=audio, start=round(start, 2), end=round(start + len(audio) / SAMPLE_RATE, 2))

    def feed(self, samples: np.ndarray) -> list[SpeechStarted | Utterance]:
        events: list[SpeechStarted | Utterance] = []
        data = np.concatenate([self._leftover, samples.astype(np.float32, copy=False)])
        n = len(data) // FRAME_SAMPLES
        self._leftover = data[n * FRAME_SAMPLES:]

        for i in range(n):
            frame = data[i * FRAME_SAMPLES:(i + 1) * FRAME_SAMPLES]
            frame_idx = self._frames_seen
            self._frames_seen += 1
            speech = self.detector(frame)

            if not self._in_speech:
                self._pre_roll.append(frame)
                self._speech_run = self._speech_run + 1 if speech else 0
                if self._speech_run >= self.cfg.start_frames:
                    self._in_speech = True
                    self._frames = list(self._pre_roll)
                    self._pre_roll.clear()
                    self._start_frame = frame_idx + 1 - len(self._frames)
                    self._voiced = self._speech_run
                    self._silence_run = 0
                    events.append(SpeechStarted(start=round(self._start_frame * FRAME_SEC, 2)))
                continue

            self._frames.append(frame)
            self._silence_run = 0 if speech else self._silence_run + 1
            self._voiced += int(speech)
            length_sec = len(self._frames) * FRAME_SEC

            if self._silence_run * FRAME_SEC * 1000 >= self.cfg.end_silence_ms:
                if utt := self._close(trim=True):
                    events.append(utt)
            elif length_sec >= self.cfg.max_utterance_sec:
                if utt := self._close(trim=False):
                    events.append(utt)
        return events

    def flush(self) -> list[Utterance]:
        """Akış kapanırken yarım kalan konuşmayı kesinleştirir."""
        if self._in_speech and self._frames:
            utt = self._close(trim=True)
            return [utt] if utt else []
        return []
