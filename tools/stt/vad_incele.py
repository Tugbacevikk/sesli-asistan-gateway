"""Konuşma algılamayı (VAD) bir çağrının bütün sesi üzerinde inceler: asistan "Sizi duyamıyorum" dediğinde hatta
konuşma var mıydı, varsa hangi seviyede ve hangi eşikte yakalanırdı?

    python tools/stt/vad_incele.py stt_teshis/1793623458.145_akis.wav --esik 0.5 --esik 0.35 --esik 0.2

Girdi: STT teşhis kaydının çağrı sesi (`STT_DEBUG_DIR` açıkken `<çağrı>_akis.wav`, 16 kHz; STT'nin duyduğu ses,
asistanın sesi yok). Çıktı:
  1. Saniye saniye seviye (dBFS) — konuşma -35 dBFS civarı ve üstü, hat gürültüsü genelde -55 ve altı.
  2. Her eşik için STT servisiyle aynı cümle bölücünün (vad.Segmenter, aynı ayarlar) bulduğu cümleler.
  3. Enerji algılayıcısının (modelsiz) bulduğu cümleler — Silero'nun kaçırdığı sesi gösterir.
  4. Silero'nun her 0,5 sn'deki en yüksek konuşma olasılığı (×100): eşiğin nereye konacağını gösterir
     (onnxruntime gerekir; faster-whisper ile zaten kurulu).
Ses dosyası repoya girmez (stt_teshis/ .gitignore'da); ses kişisel veridir, inceleme sonrası silinmeli.
"""
from __future__ import annotations

import argparse
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from sesli_asistan.stt.vad import (  # noqa: E402
    SAMPLE_RATE,
    EnergyDetector,
    Segmenter,
    SegmenterConfig,
    SpeechDetector,
    Utterance,
    make_detector,
)

FRAME = 512

def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1 or w.getframerate() != SAMPLE_RATE:
            raise SystemExit(f"{path}: 16 kHz, 16 bit, tek kanal bekleniyor (STT teşhis kaydı)")
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768


def levels(x: np.ndarray, step_s: float = 1.0) -> list[float]:
    n = int(SAMPLE_RATE * step_s)
    rms = (float(np.sqrt(np.mean(x[i:i + n] ** 2))) for i in range(0, len(x), n))
    return [round(max(-90.0, 20 * float(np.log10(r + 1e-9))), 1) for r in rms]


def segments(x: np.ndarray, det: SpeechDetector, cfg: SegmenterConfig) -> list[Utterance]:
    seg = Segmenter(det, cfg)
    out: list[Utterance] = []
    step = SAMPLE_RATE // 50                         # 20 ms parçalar (santraldeki gibi)
    for i in range(0, len(x), step):
        out += [e for e in seg.feed(x[i:i + step]) if isinstance(e, Utterance)]
    return out + seg.flush()


def silero_probs(x: np.ndarray, model: str) -> np.ndarray:
    """Her 32 ms çerçevenin Silero konuşma olasılığı (STT servisindeki modelle aynı dosya; durum çerçeveden
    çerçeveye taşınır, servisteki gibi)."""
    import onnxruntime as ort

    sess = ort.InferenceSession(model, providers=["CPUExecutionProvider"])
    names = {i.name for i in sess.get_inputs()}
    n = len(x) // FRAME
    out = np.zeros(n, dtype=np.float32)
    if {"h", "c"} <= names:                                  # v4 (sherpa-onnx dağıtımı)
        h = np.zeros((2, 1, 64), np.float32)
        c = np.zeros((2, 1, 64), np.float32)
        for i in range(n):
            p, h, c = sess.run(None, {"x": x[i * FRAME:(i + 1) * FRAME][None, :], "h": h, "c": c})
            out[i] = float(p.reshape(-1)[0])
    else:                                                    # v5: input, state, sr
        st = np.zeros((2, 1, 128), np.float32)
        sr = np.array(SAMPLE_RATE, dtype=np.int64)
        for i in range(n):
            p, st = sess.run(None, {"input": x[i * FRAME:(i + 1) * FRAME][None, :], "state": st, "sr": sr})
            out[i] = float(p.reshape(-1)[0])
    return out


def prob_timeline(probs: np.ndarray, step_s: float = 0.5) -> list[int]:
    k = max(1, round(step_s * SAMPLE_RATE / FRAME))
    return [round(float(probs[i:i + k].max()) * 100) for i in range(0, len(probs), k)]


def fmt(utts: list[Utterance]) -> str:
    return ", ".join(f"{u.start:.1f}–{u.end:.1f}" for u in utts) or "(hiç)"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Çağrı sesinde VAD incelemesi")
    ap.add_argument("wav", type=Path)
    ap.add_argument("--esik", type=float, action="append", default=[], help="Silero eşiği (tekrar verilebilir)")
    ap.add_argument("--vad-model", default="models/silero_vad.onnx")
    ap.add_argument("--sessizlik-ms", type=int, default=700, help="cümle sonu sessizliği (STT_ENDPOINT_SILENCE_MS)")
    ap.add_argument("--on-tampon-ms", type=int, default=SegmenterConfig.pre_roll_ms,
                    help="konuşma başlamadan önce alınan ses")
    ap.add_argument("--en-az-ses", type=float, default=SegmenterConfig.min_voiced_sec,
                    help="bundan kısa sesli süre atılır (öksürük / tık), sn")
    a = ap.parse_args(argv)
    x = read_wav(a.wav)
    cfg = SegmenterConfig(end_silence_ms=a.sessizlik_ms, pre_roll_ms=a.on_tampon_ms, min_voiced_sec=a.en_az_ses)
    print(f"bölücü: sessizlik {cfg.end_silence_ms} ms, ön tampon {cfg.pre_roll_ms} ms, "
          f"en az ses {cfg.min_voiced_sec} sn")

    lv = levels(x)
    print(f"{a.wav.name}: {len(x) / SAMPLE_RATE:.1f} sn, en yüksek {max(lv):.0f} dBFS, ortanca "
          f"{float(np.median(lv)):.0f} dBFS")
    print("saniye saniye seviye (dBFS):")
    for i in range(0, len(lv), 10):
        print(f"  {i:4d} sn: " + " ".join(f"{v:6.1f}" for v in lv[i:i + 10]))
    for th in a.esik or [0.5, 0.35, 0.2]:
        det = make_detector("silero", a.vad_model, th)
        name = "silero" if not isinstance(det, EnergyDetector) else "enerji (silero modeli yok!)"
        print(f"{name} eşik {th}: {fmt(segments(x, det, cfg))}")
    print(f"enerji algılayıcı: {fmt(segments(x, EnergyDetector(), cfg))}")
    if Path(a.vad_model).exists():
        try:
            tl = prob_timeline(silero_probs(x, a.vad_model))
        except ImportError:
            print("onnxruntime yok: olasılık zaman çizelgesi atlandı")
        else:
            print("Silero konuşma olasılığı, 0,5 sn'de en yüksek (×100; eşik 0,5 = 50):")
            for i in range(0, len(tl), 20):
                print(f"  {i / 2:5.0f} sn: " + " ".join(f"{v:3d}" for v in tl[i:i + 20]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
