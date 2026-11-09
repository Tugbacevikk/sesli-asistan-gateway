import numpy as np

from sesli_asistan.stt.denoiser import GentleAudioFilter

SR = 16000


def _sine(hz, sec=1.0, amp=0.3):
    t = np.arange(int(sec * SR)) / SR
    return (amp * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def _rms(x):
    return float(np.sqrt(np.mean(np.square(x))))


def test_sessizlikle_baslayan_cagrida_tik_yok():
    # durum sıfırdan başlamalı: lfilter_zi ile ilk karede tam genlikte yapay bir sıçrama oluşuyordu
    out = GentleAudioFilter(SR).process(np.zeros(512, np.float32))
    assert np.max(np.abs(out)) < 1e-6


def test_ugultu_kesilir_konusma_korunur():
    f = GentleAudioFilter(SR)
    hum = f.process(_sine(50))[SR // 2:]                    # oturduktan sonra
    assert _rms(hum) < 0.35 * _rms(_sine(50))
    f = GentleAudioFilter(SR)
    voice = f.process(_sine(1000))[SR // 2:]
    assert abs(_rms(voice) - _rms(_sine(1000))) < 0.02 * _rms(_sine(1000))


def test_parca_parca_ayni_sonuc_ve_float32():
    x = _sine(300) + _sine(40)
    whole = GentleAudioFilter(SR).process(x)
    f = GentleAudioFilter(SR)
    parts = np.concatenate([f.process(x[i:i + 320]) for i in range(0, len(x), 320)])
    assert parts.dtype == np.float32 and np.allclose(whole, parts, atol=1e-5)
