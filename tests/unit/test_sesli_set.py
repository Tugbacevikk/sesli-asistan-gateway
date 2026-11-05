"""Sesli regresyon seti aracı (tools/stt/sesli_set.py): seslendirme sahte, hat kodlaması ve karıştırma gerçek."""
import asyncio
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "stt"))
import sesli_set as ss  # noqa: E402

from sesli_asistan.gateway.core.ports import Audio  # noqa: E402


class FakeTts:
    """Metne göre farklı frekansta ton (22050 Hz, Piper'ın hızı); length_scale süreyi uzatır."""

    def __init__(self):
        self.length_scale = None
        self.calls = []

    async def synthesize(self, text):
        self.calls.append((text, self.length_scale))
        rate, seconds = 22050, 0.6 * (self.length_scale or 1.0)
        t = np.arange(int(rate * seconds)) / rate
        f = 200 + 50 * (sum(map(ord, text)) % 10)
        return Audio(pcm=(0.3 * np.sin(2 * np.pi * f * t) * 32767).astype(np.int16), rate=rate)


def test_satirlar_yorumlar_atlanir_ve_cift_ad_reddedilir(tmp_path):
    a = tmp_path / "a.tsv"
    a.write_text("# yorum\nk01.wav\tAyşe Yılmaz ile görüşmek istiyorum\n\nk02.wav\tsatış lütfen\n", encoding="utf-8")
    assert ss.read_rows([a]) == [("k01", "Ayşe Yılmaz ile görüşmek istiyorum"), ("k02", "satış lütfen")]
    with pytest.raises(ValueError, match="iki kez"):
        ss.read_rows([a, a])


def test_okuma_listeleri_okunur():
    rows = ss.read_rows([ROOT / "tests/eval/okuma_listesi.tsv", ROOT / "tests/eval/okuma_hisirti.tsv"])
    assert len(rows) >= 30 and all(text for _, text in rows)


@pytest.mark.parametrize("snr", [0.0, 10.0, 20.0])
def test_karistirma_snr_dogru(snr):
    rng = np.random.default_rng(1)
    speech = 0.1 * rng.standard_normal(16000).astype(np.float32)
    noise = rng.standard_normal(4000).astype(np.float32)           # kısa: tekrarlanır
    out = ss.mix(speech, noise, snr)
    added = out - speech
    assert len(out) == len(speech)
    assert 20 * np.log10(ss.rms(speech) / ss.rms(added)) == pytest.approx(snr, abs=0.1)


def test_karistirma_tasmaz():
    x = np.full(1000, 0.9, dtype=np.float32)
    assert np.max(np.abs(ss.mix(x, np.full(10, 0.9, dtype=np.float32), 0))) <= 0.98


@pytest.mark.parametrize(("kind", "rate"), [("g711", 8000), ("g722", 16000), ("genis", 16000)])
def test_hat_kodlamasi_sesi_korur(kind, rate):
    t = np.arange(16000) / 16000
    x = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    y, r = ss.line(x, kind)
    assert r == rate and abs(len(y) / r - 1.0) < 0.03
    # 440 Hz iki hattan da geçer: enerji korunur (kodlama gürültüsü küçük)
    assert ss.rms(y) == pytest.approx(ss.rms(x), rel=0.15)


def test_set_kurulur(tmp_path):
    rows = [("k01", "ayşe yılmaz"), ("k02", "satış lütfen")]
    speeds = [1.0, 1.1]
    tts = FakeTts()
    voiced = asyncio.run(ss.synthesize_all(tts, [t for _, t in rows], speeds))
    assert len(tts.calls) == 4 and {s for _, s in tts.calls} == {1.0, 1.1}
    bgs = [ss.Background("temiz"), ss.Background.parse("konusma:10"), ss.Background.parse("ugultu:15")]
    made = ss.build(rows, voiced, ["g711", "g722"], speeds, bgs, tmp_path)
    assert len(made) == 2 * 2 * 3 * 2
    assert ("k01_g711_h110_konusma10.wav", "ayşe yılmaz") in made
    lines = [x for x in (tmp_path / "dogrular.tsv").read_text(encoding="utf-8").splitlines() if not x.startswith("#")]
    assert len(lines) == len(made)
    with wave.open(str(tmp_path / "k02_g711_h100_temiz.wav")) as w:
        assert w.getframerate() == 8000 and w.getnchannels() == 1
    with wave.open(str(tmp_path / "k02_g722_h100_konusma10.wav")) as w:
        assert w.getframerate() == 16000
        assert w.getnframes() > 16000 * 0.6 + 16000 * 0.4      # arka plan yarım saniye önce başlar


def test_arka_plan_bicimi_denetlenir():
    import argparse
    with pytest.raises(argparse.ArgumentTypeError):
        ss.Background.parse("muzik:10")
    assert ss.Background.parse("ugultu:15").tag == "ugultu15"
