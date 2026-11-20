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


def level_gate(x: np.ndarray, sr: int, margin_db: float, frame_s: float = 0.02, hang_s: float = 0.12) -> np.ndarray:
    """Cümlenin en yüksek sesli kısmından `margin_db` daha kısık kareleri sessize alır (arkadaki ikinci konuşan).

    Telefondaki asıl konuşan ahizeye yakın olduğu için en yüksek sesli olandır. Sesli set (20.11.2026, arkada 10 dB
    kısık ikinci konuşan): kısa "he" / "destek" cevabı arkadaki uzun cümlenin içinde kayboluyor, STT onu yazıyordu.
    Başvuru seviyesi sesli karelerin 95. yüzdeliği (tek bir tık yanıltmasın); kesilen kelime uçları için `hang_s`
    kadar komşu kare tutulur, geçiş tık yapmasın diye 5 ms yumuşatılır. Üst üste binen konuşmayı ayıramaz: yalnız
    asıl konuşanın sustuğu yerlerdeki kısık sesi siler. `margin_db` <= 0: kapalı (sesi olduğu gibi döndürür)."""
    n = int(sr * frame_s)
    if margin_db <= 0 or n <= 0 or len(x) < 2 * n:
        return x
    k = len(x) // n
    frames = x[: k * n].reshape(k, n).astype(np.float64)
    db = 10 * np.log10(np.mean(frames ** 2, axis=1) + 1e-12)
    voiced = db > _GATE_VOICED_DB
    if not voiced.any():
        return x
    ref = float(np.percentile(db[voiced], 95))
    keep = db >= ref - margin_db
    hang = max(1, int(round(hang_s / frame_s)))
    keep = np.convolve(keep.astype(float), np.ones(2 * hang + 1), mode="same") > 0
    mask = np.repeat(keep.astype(np.float32), n)
    mask = np.concatenate([mask, np.full(len(x) - len(mask), mask[-1], dtype=np.float32)])
    ramp = max(1, int(sr * 0.005))
    smooth = np.convolve(mask, np.ones(ramp) / ramp, mode="same")
    return (x * smooth).astype(np.float32)


_GATE_VOICED_DB = -50.0      # bunun altı sessizlik: başvuru seviyesine katılmaz
