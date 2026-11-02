"""STT modellerini aynı kayıtlarla yan yana karşılaştırır (tasarim.md 15.3): Whisper (faster-whisper) ↔ akışlı
transducer (sherpa-onnx, ör. mihuai/turkish-stt-66m-v2).

    python tools/stt/karsilastir.py stt_teshis/*.wav \\
        --whisper models/faster-whisper-medium --whisper models/faster-whisper-large-v3-turbo \\
        --sherpa models/turkish-stt-66m-v2 --ipucu "Ayşe Yılmaz" --ipucu operatör --tsv dogrular.tsv

Girdi .wav (her örnekleme hızı; tek kanal). `--telefon`: kayıt geniş bantlıysa önce telefon hattına indirilir
(8 kHz G.711 A-law, santraldeki gibi) — STT teşhis kayıtları (STT_DEBUG_DIR) zaten hattan geldiği için GEREKMEZ.
Her model ipucusuz ve (`--ipucu` verilirse) ipuçlu çalışır (sherpa-onnx'te bağlam yanlılığı, sentencepiece ister).
`--tsv` (dosya<TAB>doğru metin) verilirse WER / CER ve tam doğru sayısı hesaplanır.

Çıktı: dosya başına her modelin metni ve süresi (tablo), özet (WER, CER, RTF) ve `--json` ile ayrıntı.
Gereken paketler: faster-whisper (stt extra), sherpa-onnx (`pip install sherpa-onnx`), jiwer (dev extra).
Modeller: Whisper → tools/models/indir.py; mihu → https://huggingface.co/mihuai/turkish-stt-66m-v2 (lisans: mihu
Community License — yalnız değerlendirme; ürüne koymak ticari sözleşme ister).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests" / "eval"))
from sesli_asistan.libs.audio import g711  # noqa: E402
from sesli_asistan.libs.audio.resample import resample  # noqa: E402

SR = 16000


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        if w.getsampwidth() != 2:
            raise ValueError(f"{path}: yalnız 16 bit PCM")
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768
        if w.getnchannels() > 1:
            x = x.reshape(-1, w.getnchannels()).mean(axis=1)
        rate = w.getframerate()
    return resample(x, rate, SR) if rate != SR else x


def telephone(x: np.ndarray) -> np.ndarray:
    """Santral hattı: 16 kHz → 8 kHz → G.711 A-law → 8 kHz → 16 kHz (Gateway / STT'nin gördüğü ses)."""
    x8 = resample(x, SR, 8000)
    x8 = g711.decode(g711.encode(x8, "pcma"), "pcma")
    return resample(x8, 8000, SR).astype(np.float32)


class Whisper:
    def __init__(self, model_dir: str, device: str):
        from sesli_asistan.stt.engine import WhisperEngine, WhisperParams

        self.name = f"whisper:{Path(model_dir).name}"
        self.engine = WhisperEngine(WhisperParams(model_dir=model_dir, device=device))

    def __call__(self, x: np.ndarray, hotwords: list[str] | None) -> str:
        return self.engine.transcribe(x, hotwords or None).text


class Sherpa:
    """Akışlı transducer (Zipformer): ses 0,1 sn'lik parçalarla verilir (santraldeki gibi), sonda 0,5 sn sessizlik.
    `hotwords` verilirse sherpa-onnx bağlam yanlılığı (contextual biasing) açılır: bpe.model'den kelime listesi
    (bpe.vocab) üretilir — `pip install sentencepiece` gerekir."""

    def __init__(self, model_dir: str, threads: int, hotwords: list[str] | None = None, score: float = 1.5):
        import sherpa_onnx

        d = Path(model_dir)
        files = {k: next(iter(sorted(d.glob(f"{k}*.onnx"))), None) for k in ("encoder", "decoder", "joiner")}
        if not all(files.values()) or not (d / "tokens.txt").exists():
            raise SystemExit(f"{d}: encoder/decoder/joiner .onnx ve tokens.txt bulunamadı")
        self.name = f"sherpa:{d.name}" + ("+ipucu" if hotwords else "")
        extra: dict[str, object] = {}
        if hotwords:
            import tempfile

            import sentencepiece as spm

            from sesli_asistan.libs.text.normalizer import turkish_lower

            sp = spm.SentencePieceProcessor(model_file=str(d / "bpe.model"))
            tmp = Path(tempfile.mkdtemp(prefix="sherpa-ipucu-"))
            (tmp / "bpe.vocab").write_text("".join(f"{sp.id_to_piece(i)}\t{sp.get_score(i)}\n"
                                                   for i in range(sp.get_piece_size())), encoding="utf-8")
            (tmp / "ipucu.txt").write_text("\n".join(turkish_lower(h) for h in hotwords) + "\n", encoding="utf-8")
            extra = {"hotwords_file": str(tmp / "ipucu.txt"), "hotwords_score": score, "modeling_unit": "bpe",
                     "bpe_vocab": str(tmp / "bpe.vocab")}
        self.rec = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=str(d / "tokens.txt"), encoder=str(files["encoder"]), decoder=str(files["decoder"]),
            joiner=str(files["joiner"]), num_threads=threads, sample_rate=SR, feature_dim=80,
            decoding_method="modified_beam_search", max_active_paths=4, **extra)

    def __call__(self, x: np.ndarray, hotwords: list[str] | None) -> str:
        s = self.rec.create_stream()
        step = SR // 10
        for i in range(0, len(x), step):
            s.accept_waveform(SR, x[i:i + step])
            while self.rec.is_ready(s):
                self.rec.decode_stream(s)
        s.accept_waveform(SR, np.zeros(SR // 2, dtype=np.float32))
        s.input_finished()
        while self.rec.is_ready(s):
            self.rec.decode_stream(s)
        r = self.rec.get_result(s)
        return (r if isinstance(r, str) else r.text).strip().lower()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="STT modellerini aynı kayıtlarla karşılaştırır")
    ap.add_argument("wav", nargs="+", type=Path)
    ap.add_argument("--whisper", action="append", default=[], help="faster-whisper model klasörü (birden çok)")
    ap.add_argument("--sherpa", action="append", default=[], help="sherpa-onnx transducer klasörü (birden çok)")
    ap.add_argument("--ipucu", action="append", default=[], help="Whisper ipucu (rehber adı); tekrar verilebilir")
    ap.add_argument("--tsv", type=Path, help="dosya<TAB>doğru metin (dosya adı ya da yolu)")
    ap.add_argument("--telefon", action="store_true", help="geniş bantlı kaydı önce 8 kHz G.711'e indir")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--json", type=Path, help="ayrıntılı sonuç dosyası")
    a = ap.parse_args(argv)
    if not a.whisper and not a.sherpa:
        ap.error("en az bir --whisper ya da --sherpa")

    refs: dict[str, str] = {}
    if a.tsv:
        with a.tsv.open(encoding="utf-8") as f:
            for row in csv.reader(f, delimiter="\t"):
                if len(row) >= 2 and row[0].strip():
                    refs[Path(row[0].strip()).name] = row[1].strip()

    engines: list[tuple[str, object, list[str] | None]] = []
    for m in a.whisper:
        w = Whisper(m, a.device)
        engines.append((w.name, w, None))
        if a.ipucu:
            engines.append((w.name + "+ipucu", w, a.ipucu))
    for m in a.sherpa:
        sh = Sherpa(m, a.threads)
        engines.append((sh.name, sh, None))
        if a.ipucu:
            try:
                shi = Sherpa(m, a.threads, a.ipucu)
                engines.append((shi.name, shi, None))
            except ImportError:
                print("not: sentencepiece yok, sherpa ipuçlu koşu atlandı (pip install sentencepiece)")

    rows = []
    audio_s = 0.0
    times = {name: 0.0 for name, _, _ in engines}
    for path in a.wav:
        x = read_wav(path)
        if a.telefon:
            x = telephone(x)
        audio_s += len(x) / SR
        row = {"dosya": path.name, "sure_sn": round(len(x) / SR, 2), "dogru": refs.get(path.name, "")}
        for name, eng, hw in engines:
            t0 = time.perf_counter()
            row[name] = eng(x, hw)  # type: ignore[operator]
            times[name] += time.perf_counter() - t0
        rows.append(row)

    names = [n for n, _, _ in engines]
    print("| dosya | " + (" doğru |" if refs else "") + " | ".join(names) + " |")
    print("|---|" + ("---|" if refs else "") + "---|" * len(names))
    for r in rows:
        cells = [r["dosya"], *([r["dogru"]] if refs else []), *(r[n] for n in names)]
        print("| " + " | ".join(c.replace("|", "/") for c in cells) + " |")

    summary: dict[str, dict[str, float]] = {}
    scored = [r for r in rows if r["dogru"]]
    for n in names:
        summary[n] = {"RTF": round(times[n] / max(audio_s, 1e-9), 3)}
    if scored:
        import jiwer
        from wer import norm

        for n in names:
            ref = [norm(r["dogru"]) for r in scored]
            hyp = [norm(r[n]) or "-" for r in scored]
            summary[n].update(WER=round(jiwer.wer(ref, hyp) * 100, 1), CER=round(jiwer.cer(ref, hyp) * 100, 1),
                              tam=sum(h == rf for h, rf in zip(hyp, ref, strict=True)))
    print(f"\nözet (n={len(rows)}, puanlanan {len(scored)}, ses {audio_s:.1f} sn):")
    for n, v in summary.items():
        print(f"  {n}: " + ", ".join(f"{k} {val}" for k, val in v.items()))
    if a.json:
        a.json.write_text(json.dumps({"satirlar": rows, "ozet": summary}, ensure_ascii=False, indent=1),
                          encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
