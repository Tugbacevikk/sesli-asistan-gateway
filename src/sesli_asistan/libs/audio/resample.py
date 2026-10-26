"""Örnekleme hızı dönüşümü (8 kHz telefon ↔ 16 kHz STT ↔ 22,05 kHz Piper).

Kaynak sistem `soxr` kullanıyordu; soxr LGPL-2.1 lisanslıdır (tasarim.md 14.5). Dağıtılan üründe
lisans yükünü azaltmak için burada scipy (BSD) kullanılır.

İki kullanım:
  - `resample()`       : tek seferlik dönüşüm (ör. bir cümlenin TTS sesi).
  - `StreamUpsampler`  : akış hâlinde gelen küçük parçalar (20 ms RTP), tam katlı artırım
                         (8000 → 16000). Filtre durumu parçalar arasında taşınır; parça parça
                         işlemek, sesin tamamını tek seferde işlemekle örnek örnek aynı sonucu
                         verir (parça sınırında tıkırtı olmaz). Bedeli: (taps-1)/2 örneklik
                         sabit gecikme (varsayılan 63 tap → 16 kHz'de ~1,9 ms).
"""
from __future__ import annotations

from math import gcd

import numpy as np
from scipy.signal import firwin, lfilter, lfilter_zi, resample_poly


def resample(x: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """float32 ya da int16 dizi → aynı türde, `dst_rate` hızında."""
    if src_rate == dst_rate or len(x) == 0:
        return x
    g = gcd(src_rate, dst_rate)
    y = resample_poly(x.astype(np.float32), dst_rate // g, src_rate // g)
    if x.dtype == np.int16:
        return np.clip(np.round(y), -32768, 32767).astype(np.int16)
    return y.astype(np.float32)


class StreamUpsampler:
    """Durumlu tam katlı artırım: sıfır ekleme + alçak geçiren FIR (lfilter, durum korunur)."""

    def __init__(self, src_rate: int, dst_rate: int, taps: int = 63):
        if dst_rate % src_rate:
            raise ValueError(f"tam katlı artırım gerekir ({src_rate} → {dst_rate})")
        self.factor = dst_rate // src_rate
        self.h = (firwin(taps, 1.0 / self.factor) * self.factor).astype(np.float64)
        self._zi = lfilter_zi(self.h, 1.0) * 0.0
        self.delay = (taps - 1) // 2

    def process(self, x: np.ndarray) -> np.ndarray:
        if self.factor == 1 or len(x) == 0:
            return x.astype(np.float32, copy=False)
        up = np.zeros(len(x) * self.factor, np.float64)
        up[:: self.factor] = x
        y, self._zi = lfilter(self.h, 1.0, up, zi=self._zi)
        return y.astype(np.float32)
