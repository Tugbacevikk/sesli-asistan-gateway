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


def test_rehberden_ipucu_gateway_ile_ayni():
    h = k.directory_hotwords(ROOT / "data" / "rehber_ornek.json")
    assert h[0] == "operatör" and "Ayşe Yılmaz" in h and "Muhasebe" in h


def test_beam_cesidi_ayari_yalniz_cagri_boyunca_degistirir():
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class P:
        beam_size: int = 2

    class Eng:
        p = P()

        def transcribe(self, x, hotwords):
            return type("R", (), {"text": f"b{self.p.beam_size}", "confidence": 0.5})()

    base = type("W", (), {"engine": Eng()})()
    assert k.WhisperVariant(base, beam_size=5)(np.zeros(1), None) == "b5"
    assert base.engine.p.beam_size == 2


def test_aralik_parcalari_ayri_satir(tmp_path, monkeypatch, capsys):
    class Fake:
        def __init__(self, model_dir, device):
            self.name = "whisper:x"

        def __call__(self, x, hotwords):
            return f"{len(x) / 16000:.1f}"

    monkeypatch.setattr(k, "Whisper", Fake)
    f = wav(tmp_path / "u.wav", 16000, 3.0)
    assert k.main([str(f), "--whisper", "m", "--aralik", "0-1", "--aralik", "1.5-3"]) == 0
    out = capsys.readouterr().out
    assert "u.wav@0-1 | 1.0" in out and "u.wav@1.5-3 | 1.5" in out
