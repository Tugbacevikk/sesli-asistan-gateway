"""Gelen ses için şeffaf dip gürültü ve şebeke uğultusu filtresi (100 Hz High-pass)."""
from __future__ import annotations

import numpy as np
import scipy.signal


class GentleAudioFilter:
    """100 Hz altındaki klima/şebeke/hava uğultusunu keser; insan konuşmasına (300-3400 Hz) hiç dokunmaz, sesi zayıflatmaz."""

    def __init__(self, sr: int = 16000):
        self.sr = sr
        nyq = sr / 2.0
        low = 100.0 / nyq
        self.b, self.a = scipy.signal.butter(2, low, btype="highpass")
        self.zi = scipy.signal.lfilter_zi(self.b, self.a)

    def process(self, chunk: np.ndarray) -> np.ndarray:
        if chunk.size == 0:
            return chunk
        filtered, self.zi = scipy.signal.lfilter(self.b, self.a, chunk, zi=self.zi)
        return filtered.astype(np.float32)
