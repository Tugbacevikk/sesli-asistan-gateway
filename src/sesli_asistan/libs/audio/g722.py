"""G.722 (geniş bant, 16 kHz ses, 64 kbit/s): santral destekliyorsa STT'ye telefon hattının 4 kHz sınırı yerine
8 kHz'e kadar ses gider (ölçüm 04.11.2026: 8 kHz G.711'de "sipariş" → "taş", "şifremi" → "şifemi"; tasarim.md ADR-027).

RTP (RFC 3551): yük türü 9; ses 16 kHz olduğu hâlde RTP saat hızı 8000 (tarihsel hata, standart böyle): 20 ms paket
= 320 örnek ses = 160 bayt, zaman damgası pakette 160 ilerler. Kodlayıcı ve çözücü durumludur (ADPCM): her çağrı ve
yön için ayrı nesne. Kitaplık: G722 (sippy/libg722, kamu malı, pip).
"""
from __future__ import annotations

import numpy as np

PAYLOAD_TYPE = 9
SAMPLE_RATE = 16000          # ses
RTP_CLOCK = 8000             # RTP zaman damgası (RFC 3551)
BIT_RATE = 64000


def _codec():
    try:
        from G722 import G722
    except ImportError as e:            # gateway extra'sında; yoksa açık hata (çağrı açılmadan)
        raise RuntimeError("G.722 için G722 paketi gerekli (pip install G722)") from e
    return G722(SAMPLE_RATE, BIT_RATE, use_numpy=False)


class Decoder:
    """RTP yükü → 16 kHz int16 PCM (little-endian bayt)."""

    def __init__(self) -> None:
        self._d = _codec()

    def decode(self, payload: bytes) -> bytes:
        return self._d.decode(payload).tobytes()


class Encoder:
    """16 kHz int16 PCM → RTP yükü (320 örnek → 160 bayt)."""

    def __init__(self) -> None:
        self._e = _codec()

    def encode(self, pcm16k: np.ndarray) -> bytes:
        if pcm16k.dtype != np.int16:
            raise TypeError("pcm16k int16 olmalı")
        return bytes(self._e.encode(np.ascontiguousarray(pcm16k)))
