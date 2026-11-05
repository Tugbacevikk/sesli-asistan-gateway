"""Sesli regresyon seti: okuma listelerinin cümleleri Piper ile seslendirilir, santral hattından geçmiş gibi kodlanır
(G.711 8 kHz / G.722 16 kHz), istenirse arkaya konuşma ya da uğultu katılır. Çıkan kayıtlar karsilastir.py ve
gorev_olc.py ile ölçülür: STT, dil modeli (ADR-028) ya da diyalog değişikliğinin **sesle** etkisi aynı setle, her
seferinde aynı biçimde görülür (metin senaryoları STT hatalarını yakalamaz: "Tuğba Çevik" → "çokağa çeviriyle").

    # Piper servisi açık olmalı (sesli-tts, :5500)
    python tools/stt/sesli_set.py tests/eval/okuma_listesi.tsv tests/eval/okuma_hisirti.tsv \\
        --hat g711 --hat g722 --hiz 0.9 --hiz 1.1 --arka konusma:10 --arka ugultu:15 --cikti sesli_set
    python tools/stt/karsilastir.py sesli_set/*.wav --tsv sesli_set/dogrular.tsv \\
        --sherpa models/turkish-stt-66m-v2 --json sonuc_sesli_set.json
    python tools/stt/gorev_olc.py sonuc_sesli_set.json --rehber data/rehber_ornek.json

Kayıt adı: `<satır>_<hat>_<hız>_<arka>.wav` (ör. k01_g711_h100_temiz.wav); `dogrular.tsv` her kaydın doğru metnini
taşır. Arka plan:
  konusma:SNR  başka bir cümle, başka hızda ve yarım saniye önce başlayarak (odadaki ikinci konuşan; canlı
               05.11.2026'da sorun olan durum). SNR dB, konuşmanın arka plana oranı: 10 = arka plan 10 dB kısık.
  ugultu:SNR   alçak geçirgen gürültü (klima, trafik).
Sentetik ses gerçek arayanın yerini tutmaz (tek ses, temiz okuma): kararlar gerçek kayıtlarla verilir; bu set
değişikliklerin **yönünü** hızlı ve tekrarlanabilir biçimde gösterir. Kayıtlar repoya girmez (`sesli_set/` gitignore).
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import random
import sys
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from sesli_asistan.libs.audio import g711, g722  # noqa: E402
from sesli_asistan.libs.audio.resample import resample  # noqa: E402

SR = 16000
LINES = ("g711", "g722", "genis")       # genis: kodlanmamış 16 kHz (üst sınır)


@dataclass(frozen=True)
class Background:
    kind: str           # "temiz" | "konusma" | "ugultu"
    snr_db: float = 0.0

    @property
    def tag(self) -> str:
        return "temiz" if self.kind == "temiz" else f"{self.kind}{self.snr_db:g}"

    @staticmethod
    def parse(text: str) -> Background:
        kind, _, snr = text.partition(":")
        if kind not in ("konusma", "ugultu") or not snr:
            raise argparse.ArgumentTypeError(f"arka plan konusma:SNR ya da ugultu:SNR olmalı: {text!r}")
        return Background(kind, float(snr))


def read_rows(paths: list[Path]) -> list[tuple[str, str]]:
    """TSV satırları (dosya<TAB>metin; # yorum) → (ad, metin); ad dosya adının gövdesi (k01)."""
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    for p in paths:
        for rec in csv.reader(p.read_text(encoding="utf-8").splitlines(), delimiter="\t"):
            if not rec or rec[0].startswith("#") or len(rec) < 2:
                continue
            name = Path(rec[0]).stem
            if name in seen:
                raise ValueError(f"{p}: {name} iki kez")
            seen.add(name)
            rows.append((name, rec[1].strip()))
    return rows


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x, dtype=np.float64)))) if len(x) else 0.0


def mix(speech: np.ndarray, noise: np.ndarray, snr_db: float) -> np.ndarray:
    """Arka planı konuşmanın RMS'ine göre `snr_db` kısık olarak ekler (gerekirse tekrarlar / keser); taşmayı önler."""
    if len(noise) == 0 or rms(noise) == 0:
        return speech
    reps = int(np.ceil(len(speech) / len(noise)))
    n = np.tile(noise, reps)[: len(speech)]
    n = n * (rms(speech) / rms(n)) / (10 ** (snr_db / 20))
    out = speech + n
    peak = float(np.max(np.abs(out))) if len(out) else 0.0
    return out / peak * 0.98 if peak > 0.98 else out


def hum(n: int, rng: np.random.Generator) -> np.ndarray:
    """Alçak geçirgen gürültü (birinci derece süzgeç, ~300 Hz köşe): uğultu."""
    white = rng.standard_normal(n).astype(np.float32)
    a = np.exp(-2 * np.pi * 300 / SR)
    out = np.empty_like(white)
    acc = 0.0
    for i, v in enumerate(white):
        acc = a * acc + (1 - a) * v
        out[i] = acc
    return out


def line(x: np.ndarray, kind: str) -> tuple[np.ndarray, int]:
    """16 kHz ses → santral hattından geçmiş ses (örnekler, hız). Kodlama gerçek kodlayıcılarla (Gateway'inkiler)."""
    if kind == "genis":
        return x, SR
    pcm = np.clip(np.round(x * 32767), -32768, 32767).astype(np.int16)
    if kind == "g722":
        enc, dec = g722.Encoder(), g722.Decoder()
        out = bytearray()
        for i in range(0, len(pcm) - len(pcm) % 320, 320):           # 20 ms paketler, Gateway'deki gibi
            out += dec.decode(enc.encode(pcm[i:i + 320]))
        return np.frombuffer(bytes(out), dtype="<i2").astype(np.float32) / 32768, SR
    if kind == "g711":
        x8 = resample(x, SR, 8000)
        return g711.decode(g711.encode(x8, "pcma"), "pcma").astype(np.float32), 8000
    raise ValueError(f"bilinmeyen hat: {kind}")


def write_wav(path: Path, x: np.ndarray, rate: int) -> None:
    pcm = np.clip(np.round(x * 32767), -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


async def synthesize_all(tts, texts: list[str], speeds: list[float]) -> dict[tuple[str, float], np.ndarray]:
    """Her (metin, hız) bir kez seslendirilir → 16 kHz float."""
    out: dict[tuple[str, float], np.ndarray] = {}
    for speed in speeds:
        tts.length_scale = speed
        for text in texts:
            if (text, speed) in out:
                continue
            audio = await tts.synthesize(text)
            x = audio.pcm.astype(np.float32) / 32768
            out[(text, speed)] = resample(x, audio.rate, SR) if audio.rate != SR else x
    return out


def build(rows: list[tuple[str, str]], voiced: dict[tuple[str, float], np.ndarray], lines: list[str],
          speeds: list[float], backgrounds: list[Background], out_dir: Path, seed: int = 7) -> list[tuple[str, str]]:
    """Kayıtları yazar; (dosya adı, doğru metin) listesi döner."""
    rng = np.random.default_rng(seed)
    pick = random.Random(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    made: list[tuple[str, str]] = []
    texts = [t for _, t in rows]
    for name, text in rows:
        for speed in speeds:
            speech = voiced[(text, speed)]
            for bg in backgrounds:
                x = speech
                if bg.kind == "konusma":
                    other = pick.choice([t for t in texts if t != text] or texts)
                    other_speed = pick.choice([s for s in speeds if s != speed] or speeds)
                    noise = voiced[(other, other_speed)]
                    lead = np.zeros(SR // 2, dtype=np.float32)          # arka plan yarım saniye önce başlar
                    x = mix(np.concatenate([lead, speech]), noise, bg.snr_db)
                elif bg.kind == "ugultu":
                    x = mix(speech, hum(len(speech), rng), bg.snr_db)
                for kind in lines:
                    y, rate = line(x, kind)
                    fname = f"{name}_{kind}_h{round(speed * 100)}_{bg.tag}.wav"
                    write_wav(out_dir / fname, y, rate)
                    made.append((fname, text))
    with (out_dir / "dogrular.tsv").open("w", encoding="utf-8", newline="") as f:
        f.write("# tools/stt/sesli_set.py çıktısı: dosya<TAB>doğru metin (repoya girmez)\n")
        for fname, text in made:
            f.write(f"{fname}\t{text}\n")
    return made


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tsv", type=Path, nargs="+", help="okuma listesi (dosya<TAB>metin)")
    ap.add_argument("--hat", choices=LINES, action="append", help="santral hattı (birden çok verilebilir; "
                    "varsayılan g711 ve g722)")
    ap.add_argument("--hiz", type=float, action="append", help="Piper length_scale (1.0 normal; 1.1 yavaş)")
    ap.add_argument("--arka", type=Background.parse, action="append", default=[],
                    help="arka plan: konusma:SNR ya da ugultu:SNR (temiz her zaman var)")
    ap.add_argument("--tts-url", default="http://127.0.0.1:5500")
    ap.add_argument("--ses", default=None, help="Piper sesi (servis birden çok ses yüklediyse)")
    ap.add_argument("--cikti", type=Path, default=Path("sesli_set"))
    ap.add_argument("--tohum", type=int, default=7)
    a = ap.parse_args(argv)

    from sesli_asistan.gateway.adapters.tts.piper_http import PiperHttpTts

    rows = read_rows(a.tsv)
    speeds = a.hiz or [1.0]
    lines = a.hat or ["g711", "g722"]
    backgrounds = [Background("temiz"), *a.arka]

    async def run() -> dict[tuple[str, float], np.ndarray]:
        tts = PiperHttpTts(a.tts_url, voice=a.ses, timeout_s=30.0)
        try:
            return await synthesize_all(tts, sorted({t for _, t in rows}), speeds)
        finally:
            await tts.aclose()

    voiced = asyncio.run(run())
    made = build(rows, voiced, lines, speeds, backgrounds, a.cikti, a.tohum)
    print(f"{len(made)} kayıt: {a.cikti}/ (doğrular: {a.cikti / 'dogrular.tsv'}); "
          f"{len(rows)} cümle × {len(speeds)} hız × {len(backgrounds)} arka plan × {len(lines)} hat")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
