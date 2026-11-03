"""tools/stt/okuma_bol.py: okuma kaydı listedeki adlarla cümlelere ayrılır, fazlalık atlanabilir."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "stt"))
import okuma_bol  # noqa: E402

from sesli_asistan.stt.vad import EnergyDetector  # noqa: E402


def _record(path: Path, n: int, rate: int = 8000) -> None:
    t = np.arange(int(0.8 * rate)) / rate
    burst = (0.3 * np.sin(2 * np.pi * 300 * t)).astype(np.float32)
    gap = np.zeros(int(1.5 * rate), np.float32)
    okuma_bol.write_wav(path, np.concatenate([gap, *[np.concatenate([burst, gap]) for _ in range(n)]]), rate)


def test_listedeki_adlarla_ve_atlayarak(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(okuma_bol, "make_detector", lambda *a: EnergyDetector())
    _record(tmp_path / "o.wav", 3)
    tsv = tmp_path / "l.tsv"
    tsv.write_text("# başlık\nk01.wav\tbir\nk02.wav\tiki\n", encoding="utf-8")
    assert okuma_bol.main([str(tmp_path / "o.wav"), "--cikti", str(tmp_path / "a"), "--tsv", str(tsv)]) == 0
    assert "UYARI" in capsys.readouterr().out
    assert sorted(p.name for p in (tmp_path / "a").iterdir()) == ["k01.wav", "k02.wav", "k03.wav"]
    okuma_bol.main([str(tmp_path / "o.wav"), "--cikti", str(tmp_path / "b"), "--tsv", str(tsv), "--atla", "2"])
    assert "UYARI" not in capsys.readouterr().out
    x, rate = okuma_bol.read_wav(tmp_path / "b" / "k02.wav")
    assert rate == 8000 and 0.9 < len(x) / rate < 2.0          # 0,8 sn ses + kenar payları


def test_pay_komsu_cumleye_tasmaz(tmp_path, monkeypatch):
    monkeypatch.setattr(okuma_bol, "make_detector", lambda *a: EnergyDetector())
    rate = 8000
    t = np.arange(int(0.8 * rate)) / rate
    burst = (0.3 * np.sin(2 * np.pi * 300 * t)).astype(np.float32)
    gap = np.zeros(int(1.2 * rate), np.float32)                 # cümle sonu sessizliği (1 sn) + az pay
    okuma_bol.write_wav(tmp_path / "o.wav", np.concatenate([gap, burst, gap, burst, gap]), rate)
    okuma_bol.main([str(tmp_path / "o.wav"), "--cikti", str(tmp_path / "a")])
    x, _ = okuma_bol.read_wav(tmp_path / "a" / "k01.wav")
    loud = np.flatnonzero(np.abs(x) > 0.1)
    assert (loud[-1] - loud[0]) / rate < 0.85                    # yalnız kendi sesi, sonrakinin başı yok
