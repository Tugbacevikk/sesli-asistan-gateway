from types import SimpleNamespace

import numpy as np

from sesli_asistan.libs.text.normalizer import TrNormalizer, turkish_lower
from sesli_asistan.stt.engine import WhisperEngine, WhisperParams, resolve_device
from sesli_asistan.stt.hallucination import HallucinationFilter
from sesli_asistan.stt.vad import EnergyDetector, Segmenter, SegmenterConfig, SpeechStarted, Utterance

SR = 16000


def _tone(sec, amp=0.3):
    t = np.arange(int(sec * SR)) / SR
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def _silence(sec):
    return np.zeros(int(sec * SR), np.float32)


# --- VAD / cümle bölücü ---

def test_konusma_baslangici_ve_cumle_sonu():
    seg = Segmenter(EnergyDetector())
    ev = seg.feed(np.concatenate([_silence(0.5), _tone(1.0), _silence(1.0)]))
    assert isinstance(ev[0], SpeechStarted) and 0.1 <= ev[0].start <= 0.5  # 350 ms ön pay dahil
    assert isinstance(ev[1], Utterance)
    assert 0.9 <= ev[1].end - ev[1].start <= 1.6  # konuşma + ön pay + kuyruk payı


def test_cok_kisa_ses_atilir_ve_asistani_susturmaz():
    # öksürük / "hı" / yankı: ne cümle olur ne de SPEECH_STARTED (barge-in) gönderilir
    seg = Segmenter(EnergyDetector())
    ev = seg.feed(np.concatenate([_silence(0.3), _tone(0.2), _silence(1.0)]))
    assert ev == []


def test_konusma_baslangici_esikten_sonra_bildirilir():
    seg = Segmenter(EnergyDetector())
    ev = seg.feed(np.concatenate([_silence(0.5), _tone(0.25)]))
    assert ev == []                                           # 250 ms: henüz söze girme sayılmaz
    ev = seg.feed(_tone(0.1))
    assert len(ev) == 1 and isinstance(ev[0], SpeechStarted)  # 300 ms'yi geçti
    assert 0.1 <= ev[0].start <= 0.5                          # başlangıç zamanı yine konuşmanın başı
    ev = seg.feed(_silence(1.0))
    assert len(ev) == 1 and isinstance(ev[0], Utterance)      # susturan her ses bir cümle sonucu da üretir


def test_uzun_konusma_bolunur():
    seg = Segmenter(EnergyDetector(), SegmenterConfig(max_utterance_sec=2.0))
    ev = seg.feed(np.concatenate([_silence(0.2), _tone(5.0)]))
    assert len([e for e in ev if isinstance(e, Utterance)]) == 2
    assert len(seg.flush()) == 1


def test_parca_parca_besleme_ayni_sonuc():
    audio = np.concatenate([_silence(0.5), _tone(1.0), _silence(1.0)])
    a = Segmenter(EnergyDetector()).feed(audio)
    seg = Segmenter(EnergyDetector())
    b = []
    for i in range(0, len(audio), 320):  # 20 ms
        b += seg.feed(audio[i:i + 320])
    assert [type(e) for e in a] == [type(e) for e in b]
    assert a[1].start == b[1].start and a[1].end == b[1].end


# --- uydurma süzgeci ---

def test_uydurma_ve_tekrar():
    f = HallucinationFilter()
    assert f.is_hallucination("Abone olun!")
    assert f.is_hallucination("  ")
    assert not f.is_hallucination("muhasebeden ayşe hanım")
    assert f.drop_repeats("evet evet evet evet tamam".split()) == ["evet", "evet", "tamam"]


# --- normalizasyon ---

def test_turkce_normalizasyon():
    assert turkish_lower("IŞIK İSTANBUL") == "ışık istanbul"
    n = TrNormalizer()
    assert n.normalize("Ayşe Hanım'ı bağlar mısınız? 3,5") == "ayşe hanım'ı bağlar mısınız 3,5"
    assert n.normalize("nasыlsın कजतच") == "nasılsın"


# --- Whisper motoru (sahte model) ---

class _FakeModel:
    def __init__(self, texts):
        self.texts, self.calls = texts, []

    def transcribe(self, samples, **kw):
        self.calls.append(kw)
        return iter([SimpleNamespace(text=t, avg_logprob=-0.1) for t in self.texts]), None


def test_motor_hotwords_ve_suzgec():
    m = _FakeModel([" Muhasebeden Ayşe Hanım.", " Abone olun.", " evet evet evet"])
    eng = WhisperEngine(WhisperParams(), model=m)
    r = eng.transcribe(_tone(1.0), hotwords=["Ayşe Yılmaz", "Muhasebe"])
    assert r.text == "muhasebeden ayşe hanım evet evet"
    assert 0.85 < r.confidence <= 1.0
    kw = m.calls[0]
    assert kw["language"] == "tr" and kw["beam_size"] == 2 and kw["vad_filter"] is False
    assert kw["hotwords"] == "Ayşe Yılmaz Muhasebe"
    assert kw["condition_on_previous_text"] is False


def test_motor_bos_ses():
    eng = WhisperEngine(WhisperParams(), model=_FakeModel(["x"]))
    assert eng.transcribe(np.zeros(0, np.float32)).text == ""


def test_cihaz_secimi():
    assert resolve_device("auto", "auto", True) == ("cuda", "float16")
    assert resolve_device("auto", "auto", False) == ("cpu", "int8")
    assert resolve_device("cuda", "float16", False) == ("cpu", "int8")


class _CudaYok:
    """Yüklenir ama ilk çözümde düşer (Antigravity STT ölçümü: cublas64_12.dll bulunamadı)."""
    model = SimpleNamespace(device="cuda")

    def transcribe(self, *a, **k):
        raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")


def test_isinmada_cuda_hatasi_auto_ise_cpuya_duser(monkeypatch):
    eng = WhisperEngine(WhisperParams(device="auto"), model=_CudaYok())
    assert eng.device == "cuda"
    monkeypatch.setattr(eng, "_load_cpu", lambda: _FakeModel(["merhaba"]))
    eng.warmup()
    assert eng.device == "cpu" and eng.transcribe(_tone(1.0)).text == "merhaba"


def test_isinmada_cuda_hatasi_cuda_istendiyse_yukselir():
    import pytest

    eng = WhisperEngine(WhisperParams(device="cuda"), model=_CudaYok())
    with pytest.raises(RuntimeError, match="cublas"):
        eng.warmup()


def test_cuda_kutuphaneleri_paket_yoksa_bos(monkeypatch):
    import importlib.util

    from sesli_asistan.stt import engine

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert engine.load_cuda_libraries() == []
