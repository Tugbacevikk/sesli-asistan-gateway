"""tools/santral/sipp/pcap_yap.py: zincir denemesi için sesler zaman çizgisinde doğru saniyeye konur."""
import struct
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "santral" / "sipp"))
import pcap_yap  # noqa: E402

from sesli_asistan.libs.audio import g711  # noqa: E402


def _wav(path: Path, rate: int, seconds: float) -> None:
    t = np.arange(int(rate * seconds)) / rate
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((0.3 * np.sin(2 * np.pi * 400 * t) * 32767).astype("<i2").tobytes())


def _decode(pcap: Path) -> np.ndarray:
    d, i, pay = pcap.read_bytes()[24:], 0, []
    while i < len(d):
        cl = struct.unpack("<IIII", d[i:i + 16])[2]
        pay.append(d[i + 16:i + 16 + cl][14 + 20 + 8 + 12:])
        i += 16 + cl
    return g711.decode(b"".join(pay), "pcma")


def test_sesler_istenen_saniyede_ve_sure_kadar(tmp_path):
    _wav(tmp_path / "a.wav", 22050, 1.0)
    assert pcap_yap.main(["--ses", f"{tmp_path / 'a.wav'}@5.2", "--sure", "10",
                          "--cikti", str(tmp_path / "z.pcap")]) == 0
    x = _decode(tmp_path / "z.pcap")
    assert abs(len(x) / 8000 - 10) < 0.05
    loud = np.flatnonzero(np.abs(x) > 0.1) / 8000
    assert 5.15 < loud.min() < 5.25 and 6.15 < loud.max() < 6.25


def test_ses_verilmezse_eski_ton():
    x = pcap_yap.tone()
    assert len(x) == 3 * 8000
