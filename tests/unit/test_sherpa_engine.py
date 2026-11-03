"""İkinci STT motoru (sherpa-onnx transducer, STT_ENGINE=sherpa): besleme, güven, seçim. Gerçek model gerekmez."""
import math

import numpy as np
import pytest

from sesli_asistan.stt.config import SttSettings
from sesli_asistan.stt.sherpa_engine import LEAD_S, TAIL_S, SherpaEngine, SherpaParams, calibrate


class Stream:
    def __init__(self):
        self.fed, self.finished = [], False

    def accept_waveform(self, sr, x):
        assert sr == 16000 and x.dtype == np.float32
        self.fed.append(len(x))

    def input_finished(self):
        self.finished = True


class Rec:
    def __init__(self, text="Ayşe Yılmaz ile görüşmek istiyorum.", probs=(-0.1, -0.2, -0.3)):
        self.text, self.probs, self.streams, self.decoded = text, list(probs), [], 0

    def create_stream(self):
        s = Stream()
        self.streams.append(s)
        return s

    def is_ready(self, s):
        return s.finished and self.decoded < 3

    def decode_stream(self, s):
        self.decoded += 1

    def get_result(self, s):
        return self.text

    def ys_probs(self, s):
        return self.probs


def test_cumle_sesi_ve_sondaki_sessizlik_verilir_metin_normallesir():
    rec = Rec()
    e = SherpaEngine(SherpaParams(), recognizer=rec)
    r = e.transcribe(np.zeros(8000, np.float64), hotwords=["Ayşe Yılmaz"])
    s = rec.streams[0]
    assert s.fed == [int(16000 * LEAD_S), 8000, int(16000 * TAIL_S)] and s.finished and rec.decoded == 3
    assert r.text == "ayşe yılmaz ile görüşmek istiyorum"
    assert r.confidence == round(calibrate(math.exp(-0.2)), 3)


def test_olasilik_yoksa_guven_bilinmiyor_bos_seste_cagri_yok():
    class Old(Rec):
        def ys_probs(self, s):
            raise AttributeError("eski sürüm")

    e = SherpaEngine(recognizer=Old())
    assert e.transcribe(np.ones(1600, np.float32)).confidence == 0.0
    rec = Rec()
    assert SherpaEngine(recognizer=rec).transcribe(np.zeros(0, np.float32)).text == "" and rec.streams == []


def test_model_yoksa_acik_hata(tmp_path):
    pytest.importorskip("sherpa_onnx")
    with pytest.raises(FileNotFoundError, match="turkish-stt-66m-v2"):
        SherpaEngine(SherpaParams(model_dir=str(tmp_path)))


def test_motor_ayardan_secilir(monkeypatch):
    from sesli_asistan.stt import server

    made = {}
    monkeypatch.setattr(server, "SherpaEngine", lambda p: made.setdefault("sherpa", p))
    monkeypatch.setattr(server, "WhisperEngine", lambda p: made.setdefault("whisper", p))
    server.load_engine(SttSettings(_env_file=None, engine="sherpa", sherpa_dir="m/x", sherpa_threads=3))
    server.load_engine(SttSettings(_env_file=None, engine="whisper"))
    assert made["sherpa"].model_dir == "m/x" and made["sherpa"].threads == 3
    assert made["whisper"].model_dir.endswith("faster-whisper-medium")
    assert SttSettings(_env_file=None).engine == "sherpa"          # varsayılan mihu (ADR-021, 03.11.2026)


def test_guven_whisper_olcegine_cevrilir():
    # ölçümdeki en iyi ham eşikler (teyit 0,5 / tekrar 0,3) Gateway'in eşiklerine (0,75 / 0,5) denk düşer
    assert calibrate(0.5) == 0.75 and calibrate(0.3) == 0.5
    assert calibrate(0.0) == 0.0 and calibrate(1.0) == 1.0
    xs = [i / 100 for i in range(101)]
    ys = [calibrate(x) for x in xs]
    assert ys == sorted(ys)                                     # tekdüze: sıralama korunur
    assert calibrate(0.49) < 0.75 <= calibrate(0.5) and calibrate(0.29) < 0.5 <= calibrate(0.3)
