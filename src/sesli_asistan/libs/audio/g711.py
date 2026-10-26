"""G.711 (ITU-T) A-law / μ-law kodlama ve çözme — yalnızca numpy.

Kaynak: Vietnamese-speech-to-text-models `infrastructure/rtp.py` (çözme tabloları, aynı formüller).
Eklenen: kodlama (Gateway asistan sesini santrale RTP ile geri gönderir) ve int16 arayüzü.
Kodlama ITU-T g711.c referans algoritmasıdır. Python 3.13'te `audioop` kaldırıldığı için
standart kütüphaneye güvenilmez.

Biçimler:
  PCMU = μ-law, RTP yük türü 0, 8 kHz
  PCMA = A-law, RTP yük türü 8, 8 kHz (Türkiye/Avrupa şebekesi; varsayılan)
"""
from __future__ import annotations

import numpy as np

CODECS = ("pcma", "pcmu")
PAYLOAD_TYPE = {"pcmu": 0, "pcma": 8}
CODEC_OF_PAYLOAD = {0: "pcmu", 8: "pcma"}


def _ulaw_table() -> np.ndarray:
    u = ~np.arange(256, dtype=np.int32) & 0xFF
    sign, exp, mant = u & 0x80, (u >> 4) & 0x07, u & 0x0F
    mag = (((mant << 3) + 0x84) << exp) - 0x84
    return np.where(sign, -mag, mag).astype(np.int16)


def _alaw_table() -> np.ndarray:
    a = np.arange(256, dtype=np.int32) ^ 0x55
    sign, exp, mant = a & 0x80, (a >> 4) & 0x07, a & 0x0F
    mag = np.where(exp == 0, (mant << 4) + 8, ((mant << 4) + 0x108) << np.maximum(exp - 1, 0))
    return np.where(sign, mag, -mag).astype(np.int16)


ULAW_INT16 = _ulaw_table()
ALAW_INT16 = _alaw_table()
_TABLES = {"pcmu": ULAW_INT16, "pcma": ALAW_INT16}
_TABLES_F32 = {k: (v / 32768.0).astype(np.float32) for k, v in _TABLES.items()}


def decode_int16(payload: bytes, codec: str) -> np.ndarray:
    """G.711 baytları → int16 PCM."""
    return _table(_TABLES, codec)[np.frombuffer(payload, dtype=np.uint8)]


def decode(payload: bytes, codec: str) -> np.ndarray:
    """G.711 baytları → float32 PCM (-1..1). STT hattının giriş biçimi."""
    return _table(_TABLES_F32, codec)[np.frombuffer(payload, dtype=np.uint8)]


# Bölüm (segment) üst sınırları: g711.c seg_uend / seg_aend
_SEG_UEND = np.array([0x3F, 0x7F, 0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF, 0x1FFF])
_SEG_AEND = np.array([0x1F, 0x3F, 0x7F, 0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF])


def _ulaw_encode(pcm: np.ndarray) -> np.ndarray:
    x = pcm.astype(np.int32) >> 2
    mask = np.where(x < 0, 0x7F, 0xFF)
    x = np.minimum(np.abs(x), 8159) + 33          # CLIP + (BIAS >> 2)
    seg = np.searchsorted(_SEG_UEND, x, side="left")
    seg_c = np.minimum(seg, 7)
    u = (seg_c << 4) | ((x >> (seg_c + 1)) & 0x0F)
    u = np.where(seg >= 8, 0x7F, u)
    return ((u ^ mask) & 0xFF).astype(np.uint8)


def _alaw_encode(pcm: np.ndarray) -> np.ndarray:
    x = pcm.astype(np.int32) >> 3
    mask = np.where(x >= 0, 0xD5, 0x55)
    x = np.where(x >= 0, x, -x - 1)
    seg = np.searchsorted(_SEG_AEND, x, side="left")
    seg_c = np.minimum(seg, 7)
    shift = np.where(seg_c < 2, 1, seg_c)
    a = (seg_c << 4) | ((x >> shift) & 0x0F)
    a = np.where(seg >= 8, 0x7F, a)
    return ((a ^ mask) & 0xFF).astype(np.uint8)


def encode_int16(pcm: np.ndarray, codec: str) -> bytes:
    """int16 PCM → G.711 baytları."""
    if codec == "pcmu":
        return _ulaw_encode(pcm).tobytes()
    if codec == "pcma":
        return _alaw_encode(pcm).tobytes()
    raise ValueError(f"desteklenmeyen codec: {codec} (beklenen: {CODECS})")


def encode(samples: np.ndarray, codec: str) -> bytes:
    """float32 PCM (-1..1) → G.711 baytları."""
    pcm = np.clip(np.round(samples * 32768.0), -32768, 32767).astype(np.int16)
    return encode_int16(pcm, codec)


def _table(tables: dict[str, np.ndarray], codec: str) -> np.ndarray:
    try:
        return tables[codec]
    except KeyError:
        raise ValueError(f"desteklenmeyen codec: {codec} (beklenen: {CODECS})") from None
