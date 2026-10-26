import numpy as np
import pytest

from sesli_asistan.libs.audio import g711


def test_bilinen_degerler():
    # μ-law 0xFF ve A-law 0xD5 sıfıra en yakın kodlardır (sessizlik)
    assert g711.ULAW_INT16[0xFF] == 0
    assert g711.ALAW_INT16[0xD5] == 8
    assert g711.ULAW_INT16[0x00] == -32124
    assert g711.ALAW_INT16[0x55] == -8


@pytest.mark.parametrize("codec", ["pcma", "pcmu"])
def test_tum_kodlar_kodla_coz_ayni_kalir(codec):
    # her G.711 kodu çözülüp yeniden kodlanınca aynı kod çıkmalı (μ-law'da +0/-0 hariç)
    codes = np.arange(256, dtype=np.uint8).tobytes()
    pcm = g711.decode_int16(codes, codec)
    again = g711.decode_int16(g711.encode_int16(pcm, codec), codec)
    assert np.array_equal(pcm, again)


@pytest.mark.parametrize("codec", ["pcma", "pcmu"])
def test_sinyal_hatasi_kucuk(codec):
    t = np.arange(8000) / 8000
    x = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    y = g711.decode(g711.encode(x, codec), codec)
    snr = 10 * np.log10(np.sum(x ** 2) / np.sum((x - y) ** 2))
    assert snr > 30  # G.711 ~38 dB


def test_sinirda_tasma_yok():
    x = np.array([-32768, 32767, -1, 0, 1], np.int16)
    for codec in g711.CODECS:
        y = g711.decode_int16(g711.encode_int16(x, codec), codec)
        assert np.all(np.sign(y[:2]) == [-1, 1])


def test_bilinmeyen_codec():
    with pytest.raises(ValueError):
        g711.decode(b"\x00", "opus")
