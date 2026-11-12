"""Arayüzün mikrofon yeniden örnekleyicisi (ui.html → DSP): katlanma (aliasing) olmamalı.

27.10.2026 denemesi: ilk sürümdeki kutu süzgeci 4–8 kHz sesini konuşma bandına yalnızca ~8 dB zayıflatarak katlıyordu
("s, ş, ç" sesleri bozuluyor, Whisper "aygül defarpan" gibi yanlış yazıyordu). JavaScript kodu Node ile çalıştırılır.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node yok")


def run_dsp(rate: int, tones: list[float], tmp_path, out_rate: int = 8000) -> np.ndarray:
    html = (ROOT / "src/sesli_asistan/gateway/api/ui.html").read_text(encoding="utf-8")
    dsp = re.search(r"const DSP = `(.*?)`;", html, re.S).group(1)
    script = tmp_path / "dsp.js"
    script.write_text(dsp + f"""
const sr = {rate}, N = sr * 2, x = new Float32Array(N), tones = {json.dumps(tones)};
for (let i = 0; i < N; i++) for (const f of tones) x[i] += 0.3 * Math.sin(2 * Math.PI * f * i / sr);
const out = [], r = new Resampler(sr, {out_rate});
for (let i = 0; i < N; i += 128) r.push(x.subarray(i, i + 128), out);
process.stdout.write(JSON.stringify(out));
""", encoding="utf-8")
    return np.array(json.loads(subprocess.run(["node", str(script)], capture_output=True, check=True,
                                              text=True).stdout))


@pytest.mark.parametrize("rate", [44100, 48000])
def test_mikrofon_8khz_katlanma_yok(rate, tmp_path):
    y = run_dsp(rate, [1000, 5500, 6800], tmp_path)
    assert abs(len(y) - 16000) <= 1                       # 2 sn → 8 kHz
    seg = y[2000:10000]
    spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
    f = np.fft.rfftfreq(len(seg), 1 / 8000)

    def db(hz):
        return 20 * np.log10(spec[np.argmin(abs(f - hz))] / spec[np.argmin(abs(f - 1000))])

    assert db(2500) < -60 and db(1200) < -60              # 5,5 ve 6,8 kHz konuşma bandına katlanmadı
    assert 0.25 < np.sqrt(2) * seg.std() < 0.35           # 1 kHz konuşma bandı korunuyor (genlik 0,3)


@pytest.mark.parametrize("rate", [44100, 48000])
def test_mikrofon_16khz_genis_bant(rate, tmp_path):
    # varsayılan yol (G.722 hattı gibi 16 kHz): 5,5 kHz konuşma bandında kalır, 10 kHz 6 kHz'e katlanmaz
    y = run_dsp(rate, [1000, 5500, 10000], tmp_path, out_rate=16000)
    assert abs(len(y) - 32000) <= 1                       # 2 sn → 16 kHz
    seg = y[4000:20000]
    spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
    f = np.fft.rfftfreq(len(seg), 1 / 16000)

    def db(hz):
        return 20 * np.log10(spec[np.argmin(abs(f - hz))] / spec[np.argmin(abs(f - 1000))])

    assert db(5500) > -3                                  # 8 kHz hatta kaybolan s / ş bandı burada korunur
    assert db(6000) < -60                                 # 10 kHz katlanmadı
