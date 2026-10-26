import numpy as np
from scipy.signal import lfilter

from sesli_asistan.libs.audio.resample import StreamUpsampler, resample


def test_parca_parca_isleme_tek_seferle_birebir_ayni():
    rng = np.random.default_rng(0)
    x = rng.standard_normal(8000).astype(np.float32) * 0.1
    up = StreamUpsampler(8000, 16000)
    parts = [up.process(x[i:i + 160]) for i in range(0, len(x), 160)]  # 20 ms RTP parçaları
    y = np.concatenate(parts)

    ref_up = np.zeros(len(x) * 2)
    ref_up[::2] = x
    ref = lfilter(up.h, 1.0, ref_up)
    assert len(y) == 16000
    assert np.allclose(y, ref, atol=1e-6)


def test_sinus_korunur():
    t = np.arange(8000) / 8000
    x = np.sin(2 * np.pi * 300 * t).astype(np.float32)
    up = StreamUpsampler(8000, 16000)
    y = up.process(x)[up.delay:]
    t2 = np.arange(len(y)) / 16000
    ref = np.sin(2 * np.pi * 300 * t2)
    assert np.max(np.abs(y[200:-200] - ref[200:-200])) < 0.02


def test_tek_seferlik_tur_korunur():
    x16 = (np.ones(220) * 1000).astype(np.int16)
    y = resample(x16, 22050, 8000)
    assert y.dtype == np.int16 and abs(len(y) - 80) <= 1
