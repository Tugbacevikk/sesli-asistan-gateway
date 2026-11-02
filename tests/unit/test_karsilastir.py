"""STT karşılaştırma aracı (tools/stt/karsilastir.py): okuma, telefon hattı benzetimi, tablo ve WER."""
import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "stt"))
import karsilastir as k  # noqa: E402


def wav(path, rate=8000, seconds=0.5):
    t = np.arange(int(rate * seconds)) / rate
    x = (0.3 * np.sin(2 * np.pi * 440 * t) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(x.tobytes())
    return path


def test_okuma_16khz_ve_telefon_hatti(tmp_path):
    x = k.read_wav(wav(tmp_path / "a.wav", 8000))
    assert len(x) == 8000 and x.dtype == np.float32           # 0,5 sn @16 kHz
    y = k.telephone(k.read_wav(wav(tmp_path / "b.wav", 16000)))
    assert len(y) == 8000 and abs(np.abs(y).max() - 0.3) < 0.05


def test_tablo_ve_wer(tmp_path, monkeypatch, capsys):
    class Fake:
        def __init__(self, model_dir, device):
            self.name = f"whisper:{Path(model_dir).name}"

        def __call__(self, x, hotwords):
            return "ayşe yılmaz" if hotwords else "aşağıya yılmaz"

    monkeypatch.setattr(k, "Whisper", Fake)
    f = wav(tmp_path / "c1.wav")
    tsv = tmp_path / "d.tsv"
    tsv.write_text("c1.wav\tAyşe Yılmaz\n", encoding="utf-8")
    out = tmp_path / "s.json"
    args = [str(f), "--whisper", "m/medium", "--ipucu", "Ayşe Yılmaz", "--tsv", str(tsv), "--json", str(out)]
    assert k.main(args) == 0
    text = capsys.readouterr().out
    assert "| c1.wav | Ayşe Yılmaz | aşağıya yılmaz | ayşe yılmaz |" in text
    import json
    s = json.loads(out.read_text())["ozet"]
    assert s["whisper:medium"]["WER"] == 50.0 and s["whisper:medium+ipucu"]["WER"] == 0.0
    assert s["whisper:medium+ipucu"]["tam"] == 1
