"""Türkçe STT doğruluk ölçümü (WER / CER) — kayıtlar ve doğru metinler TSV'de.

Kaynak: Vietnamese-speech-to-text-models `benchmarks/yerel_dogruluk_testi.py` → `norm()` ve jiwer kullanımı.
Normalizasyon kaynakla birebir aynıdır; böylece buradaki sonuçlar kaynak sistemin `docs/olcumler.md`
değerleriyle (ör. medium 8 kHz %17–22) karşılaştırılabilir.

TSV sütunları: dosya, metin (insan yazımı doğru metin). Yollar TSV'ye göre olabilir.

    python tests/eval/wer.py kayitlar.tsv --model-dir models/faster-whisper-medium \
        --vad models/silero_vad.onnx

Ölçüm kuralı (tasarim.md 15.2): sonuçla birlikte kayıt sayısı (n), donanım, commit ve ayarlar yazılır;
tek kayıtlı sonuç "yön gösterir" diye işaretlenir.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from sesli_asistan.libs.text.normalizer import turkish_lower  # noqa: E402


def norm(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    return " ".join(re.sub(r"[^\w\s]", " ", turkish_lower(text)).split())


def main(argv=None) -> int:
    import jiwer
    import soundfile as sf

    from sesli_asistan.libs.audio.resample import resample
    from sesli_asistan.stt.engine import WhisperEngine, WhisperParams
    from sesli_asistan.stt.vad import Segmenter, make_detector

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tsv")
    ap.add_argument("--model-dir", default="models/faster-whisper-medium")
    ap.add_argument("--vad", default="models/silero_vad.onnx")
    ap.add_argument("--beam", type=int, default=2)
    ap.add_argument("--device", default="auto")
    ap.add_argument("--telefon", action="store_true", help="sesi önce 8 kHz'e indir (telefon bandı)")
    ap.add_argument("--cikti", default="sonuc_wer.json")
    a = ap.parse_args(argv)

    tsv = Path(a.tsv)
    rows = list(csv.DictReader(tsv.open(encoding="utf-8"), delimiter="\t"))
    eng = WhisperEngine(WhisperParams(model_dir=a.model_dir, beam_size=a.beam, device=a.device))
    refs, hyps, sure, ses = [], [], 0.0, 0.0
    for r in rows:
        f = Path(r["dosya"].replace("\\", "/"))
        f = f if f.is_absolute() or f.exists() else tsv.parent / f
        audio, sr = sf.read(str(f), dtype="float32", always_2d=True)
        audio = audio.mean(axis=1)
        if a.telefon:
            audio = resample(resample(audio, sr, 8000), 8000, 16000)
        else:
            audio = resample(audio, sr, 16000)
        ses += len(audio) / 16000
        seg = Segmenter(make_detector("silero", a.vad))
        utts = [e for e in seg.feed(audio) if hasattr(e, "audio")] + seg.flush()
        t = time.perf_counter()
        hyp = " ".join(eng.transcribe(u.audio).text for u in utts)
        sure += time.perf_counter() - t
        refs.append(norm(r["metin"]))
        hyps.append(norm(hyp))
    out = {"n": len(rows), "WER": round(jiwer.wer(refs, hyps) * 100, 2),
           "CER": round(jiwer.cer(refs, hyps) * 100, 2), "RTF": round(sure / max(ses, 1e-9), 3),
           "ayarlar": vars(a), "cihaz": eng.device}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    Path(a.cikti).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
