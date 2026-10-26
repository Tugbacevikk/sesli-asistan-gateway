"""TTS denemesi ve ölçümü: Piper servisine metin seslendirtir, telefonda duyulacak hâliyle (8 kHz) WAV'a yazar.

    python tools/tts/seslendir.py --metin "Sizi Muhasebe birimine aktarıyorum."
    python tools/tts/seslendir.py --anonslar --cikti ses_ornekleri
    python tools/tts/seslendir.py --olcum --tekrar 5            # gecikme ölçümü (sonuc_tts.json)
    python tools/tts/seslendir.py --url http://127.0.0.1:5500 --yol / --bicim plain --metin "deneme"

Ölçülenler (tasarim.md 11.1, hedef: ilk ses ≤ 250 ms):
  - tts_sn       : tek cümlenin Piper'da seslendirilme süresi (önbelleksiz)
  - rtf          : tts_sn / ses süresi (1'in altı = gerçek zamandan hızlı)
  - ilk_ses_sn   : Speaker.say() çağrısından ilk sesin çalınmaya başlamasına kadar (cümle cümle boru hattı)
  - önbellek     : aynı metin ikinci kez istendiğinde süre
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from sesli_asistan.gateway.adapters.tts.cache import CachingTts  # noqa: E402
from sesli_asistan.gateway.adapters.tts.piper_http import PiperHttpTts  # noqa: E402
from sesli_asistan.gateway.config import GatewaySettings  # noqa: E402
from sesli_asistan.gateway.core.dialog import DialogConfig, fixed_prompts  # noqa: E402
from sesli_asistan.gateway.core.speech import (  # noqa: E402
    LINE_RATE,
    PromptLibrary,
    Speaker,
    prepare_for_tts,
    split_sentences,
)

OLCUM_METINLERI = [
    "Sizi Ayşe Yılmaz'a aktarıyorum.",
    "Sizi Muhasebe birimine aktarıyorum.",
    "Muhasebede iki kişi var: Ayşe Yılmaz ve Burak Çelik. Hangisiyle görüşmek istersiniz?",
    "Ayşe Hanım şu an meşgul. Başka birine mi aktarayım, yoksa sizi operatöre mi bağlayayım?",
    "Aradığınız kişiyi rehberde bulamadım. Görüşmek istediğiniz kişinin adını ya da birimini tekrar söyler misiniz?",
]


class FileOut:
    """AudioOutPort: çalınan sesi biriktirir (gerçek zamanlı bekleme yok)."""

    def __init__(self):
        self.parts: list[np.ndarray] = []

    async def play(self, pcm8k) -> int:
        self.parts.append(pcm8k)
        return len(pcm8k) // 160

    def write(self, path: Path) -> float:
        pcm = np.concatenate(self.parts) if self.parts else np.zeros(0, np.int16)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(LINE_RATE)
            w.writeframes(pcm.astype("<i2").tobytes())
        return len(pcm) / LINE_RATE


def pct(v, p):
    s = sorted(v)
    return round(s[min(len(s) - 1, round(p / 100 * (len(s) - 1)))], 3) if s else None


async def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--metin", action="append", default=[])
    ap.add_argument("--anonslar", action="store_true", help="sabit anonsları (karşılama, operatör…) seslendir")
    ap.add_argument("--olcum", action="store_true", help="örnek cümlelerle gecikme ölçümü")
    ap.add_argument("--tekrar", type=int, default=3)
    ap.add_argument("--cikti", default="ses_ornekleri")
    ap.add_argument("--url", default=None)
    ap.add_argument("--yol", default=None)
    ap.add_argument("--bicim", choices=["json", "plain", "query"], default=None)
    ap.add_argument("--ses", default=None, help="Piper ses adı (voice)")
    a = ap.parse_args(argv)

    s = GatewaySettings()
    s.tts_url = a.url or s.tts_url
    s.tts_path = a.yol or s.tts_path
    s.tts_format = a.bicim or s.tts_format
    s.tts_voice = a.ses or s.tts_voice
    piper = PiperHttpTts(s.tts_url, s.tts_path, s.tts_format, s.tts_voice, s.tts_length_scale, s.tts_timeout_s)
    out_dir = Path(a.cikti)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"TTS: {s.tts_url}{s.tts_path} ({s.tts_format}) · çıktı: {out_dir}/")

    texts = dict(enumerate(a.metin, 1))
    if a.anonslar:
        texts.update(fixed_prompts(DialogConfig(organization=s.organization_name)))
    if a.olcum:
        texts.update({f"olcum{i}": t for i, t in enumerate(OLCUM_METINLERI, 1)})
    if not texts:
        ap.error("--metin, --anonslar ya da --olcum verin")

    # 1) Ham TTS süresi (önbelleksiz), cümle başına
    raw = []
    for _ in range(a.tekrar if a.olcum else 1):
        for t in texts.values():
            for sent in split_sentences(t):
                t0 = time.perf_counter()
                audio = await piper.synthesize(prepare_for_tts(sent))
                dt = time.perf_counter() - t0
                raw.append({"cumle": sent, "tts_sn": round(dt, 3), "ses_sn": round(audio.seconds, 2),
                            "rtf": round(dt / max(audio.seconds, 1e-6), 3), "hz": audio.rate})

    # 2) Speaker ile (cümle cümle boru hattı + önbellek), WAV'a yaz
    cached = CachingTts(piper)
    speaker = Speaker(cached, PromptLibrary(), timeout_s=s.tts_timeout_s)
    rows = []
    for name, t in texts.items():
        cold, warm = FileOut(), FileOut()
        r1 = await speaker.say(t, cold)
        r2 = await speaker.say(t, warm)  # ikinci kez: önbellekten
        dur = cold.write(out_dir / f"{name}.wav")
        rows.append({"ad": str(name), "metin": t, "seslendirilen": prepare_for_tts(t), "cumle": r1.sentences,
                     "ok": r1.ok, "hata": r1.error, "ses_sn": round(dur, 2),
                     "ilk_ses_sn": round(r1.first_audio_s or 0, 3),
                     "ilk_ses_onbellek_sn": round(r2.first_audio_s or 0, 3)})
        print(f"  {name}: {'✓' if r1.ok else '✗ ' + r1.error}  ilk ses {rows[-1]['ilk_ses_sn']} sn "
              f"(önbellekten {rows[-1]['ilk_ses_onbellek_sn']} sn), {dur:.1f} sn ses → {out_dir}/{name}.wav")

    tts = [r["tts_sn"] for r in raw]
    report = {"url": f"{s.tts_url}{s.tts_path}", "bicim": s.tts_format, "ses": s.tts_voice,
              "ozet": {"cumle_sayisi": len(raw), "tts_sn_p50": round(statistics.median(tts), 3) if tts else None,
                       "tts_sn_p95": pct(tts, 95), "rtf_p50": round(statistics.median([r["rtf"] for r in raw]), 3),
                       "ilk_ses_sn_p50": round(statistics.median([r["ilk_ses_sn"] for r in rows]), 3),
                       "ornekleme_hz": sorted({r["hz"] for r in raw})},
              "cumleler": raw, "metinler": rows}
    Path("sonuc_tts.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["ozet"], ensure_ascii=False))
    print("ayrıntı: sonuc_tts.json")
    await piper.aclose()
    return 0 if all(r["ok"] for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
