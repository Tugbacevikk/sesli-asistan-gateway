"""Konuşma algılamayı (VAD) bir çağrının bütün sesi üzerinde inceler: asistan "Sizi duyamıyorum" dediğinde hatta
konuşma var mıydı, varsa hangi seviyede ve hangi eşikte yakalanırdı?

    python tools/stt/vad_incele.py stt_teshis/1793623458.145_akis.wav --esik 0.5 --esik 0.35 --esik 0.2

Girdi: STT teşhis kaydının çağrı sesi (`STT_DEBUG_DIR` açıkken `<çağrı>_akis.wav`, 16 kHz; STT'nin duyduğu ses,
asistanın sesi yok). Çıktı:
  1. Saniye saniye seviye (dBFS) — konuşma -35 dBFS civarı ve üstü, hat gürültüsü genelde -55 ve altı.
  2. Her eşik için STT servisiyle aynı cümle bölücünün (vad.Segmenter, aynı ayarlar) bulduğu cümleler.
  3. Enerji algılayıcısının (modelsiz) bulduğu cümleler — Silero'nun kaçırdığı sesi gösterir.
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


def fmt(utts: list[Utterance]) -> str:
    return ", ".join(f"{u.start:.1f}–{u.end:.1f}" for u in utts) or "(hiç)"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Çağrı sesinde VAD incelemesi")
    ap.add_argument("wav", type=Path)
    ap.add_argument("--esik", type=float, action="append", default=[], help="Silero eşiği (tekrar verilebilir)")
    ap.add_argument("--vad-model", default="models/silero_vad.onnx")
    ap.add_argument("--sessizlik-ms", type=int, default=700, help="cümle sonu sessizliği (STT_ENDPOINT_SILENCE_MS)")
    a = ap.parse_args(argv)
    x = read_wav(a.wav)
    cfg = SegmenterConfig(end_silence_ms=a.sessizlik_ms)

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
