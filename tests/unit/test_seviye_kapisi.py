"""Seviye kapısı (stt/denoiser.py level_gate): asıl konuşanın sustuğu yerdeki kısık ses sessize alınır."""
from __future__ import annotations

import numpy as np

from sesli_asistan.stt.denoiser import level_gate

SR = 16000


def _ton(sure: float, db: float, hz: float = 300.0) -> np.ndarray:
    t = np.arange(int(SR * sure)) / SR
    return (10 ** (db / 20) * np.sqrt(2) * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def _db(x: np.ndarray) -> float:
    return float(10 * np.log10(np.mean(x.astype(np.float64) ** 2) + 1e-12))


def test_kisik_arka_ses_silinir_asil_ses_kalir() -> None:
    arka, asil = _ton(1.0, -40, 500), _ton(0.5, -20)
    x = np.concatenate([arka, asil, arka])
    y = level_gate(x, SR, 10)
    assert _db(y[: int(0.8 * SR)]) < -70                                  # arkadaki ses (asıl sözden uzak kısım)
    assert abs(_db(y[int(1.05 * SR):int(1.45 * SR)]) - _db(asil)) < 0.5      # asıl söz olduğu gibi


def test_kapali_iken_dokunmaz() -> None:
    x = np.concatenate([_ton(0.5, -40), _ton(0.5, -20)])
    assert level_gate(x, SR, 0) is x


def test_esik_genisse_hepsi_kalir() -> None:
    x = np.concatenate([_ton(0.5, -26), _ton(0.5, -20)])
    np.testing.assert_allclose(level_gate(x, SR, 12)[SR // 10:-SR // 10], x[SR // 10:-SR // 10], atol=1e-6)


def test_sessiz_ses_degismez() -> None:
    x = np.zeros(SR, dtype=np.float32)
    np.testing.assert_array_equal(level_gate(x, SR, 10), x)
