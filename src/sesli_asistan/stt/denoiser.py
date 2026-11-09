"""Gelen ses için alçak frekans süzgeci (yüksek geçiren, varsayılan 100 Hz): klima, şebeke ve rüzgâr uğultusu.

Konuşmaya (yaklaşık 300–3400 Hz) dokunmaz, sesi zayıflatmaz. G.711 dar bant hattında 100 Hz'in altı zaten büyük ölçüde
kesik geldiği için etkisi en çok G.722 ve tarayıcı mikrofonunda görülür. `STT_HIGHPASS_HZ=0` ile kapatılır.
"""
from __future__ import annotations

import numpy as np
import scipy.signal


class GentleAudioFilter:
    """Akışlı 2. derece Butterworth yüksek geçiren süzgeç; çağrı başına bir tane (durum çağrılar arasında taşınmaz)."""

    def __init__(self, sr: int = 16000, cutoff_hz: float = 100.0):
        self.sr = sr
        self.b, self.a = scipy.signal.butter(2, cutoff_hz / (sr / 2.0), btype="highpass")
        # Durum sıfırdan başlar. `lfilter_zi` "önceki ses tam genlikte sabitti" varsayar ve her çağrının ilk
        # karesinde yapay bir tık üretir (VAD bunu konuşma başlangıcı sayabilir).
        self.zi = np.zeros(max(len(self.a), len(self.b)) - 1)

    def process(self, chunk: np.ndarray) -> np.ndarray:
        if chunk.size == 0:
            return chunk
        filtered, self.zi = scipy.signal.lfilter(self.b, self.a, chunk, zi=self.zi)
        return filtered.astype(np.float32)
