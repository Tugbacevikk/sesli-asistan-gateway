"""TTS okunuş denetimi: asistanın söyleyebileceği cümleler seslendirilir, telefon hattından geçirilir ve STT'ye geri
dinletilir. Geri yazılan metin söyleneceğe uzaksa (karakter hata oranı, CER) cümle "şüpheli" sayılır; en şüpheliler
dinlenmek üzere WAV olarak yazılır. Yüzlerce adı tek tek dinlemek yerine yalnız şüphelileri dinlemek için.

    python tools/tts/okunus_denetim.py --rehber data/sirket_rehber.json --rehber data/demo_rehber.json \\
        --sherpa models/turkish-stt-66m-v2 --cikti okunus --en-kotu 40

Cümleler: sabit anonslar; her kişi ve birim hattı için aktarma ve teyit cümlesi ("Sizi Ayşe Yılmaz'a aktarıyorum.");
her birim için "X'den kiminle görüşmek istersiniz?"; her unvan için "Hangi birimdeki X?".
Ayrıca seslendirmeden önce metin denetlenir (TTS'e giden hâli, `prepare_for_tts` sonrası): büyük harf kısaltma
("VHKİ"), noktalı kısaltma ("Şb."), kalan rakam, Türkçede olmayan harf (q, w, x). Bunlar STT'siz de raporlanır
(`--yalniz-metin`: TTS / STT gerekmez).

Not: STT'nin kendi hatası da CER'e karışır (özellikle nadir soyadlarda); şüpheli liste kesin hata listesi değildir,
dinleme sırasıdır. Ölçüt telefon sesidir (8 kHz G.711): arayanın duyduğu.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests" / "eval"))
from sesli_asistan.gateway.composition import build_agent, build_directory  # noqa: E402
from sesli_asistan.gateway.config import GatewaySettings  # noqa: E402
from sesli_asistan.gateway.core.dialog import DialogConfig, fixed_prompts  # noqa: E402
from sesli_asistan.gateway.core.models import Contact  # noqa: E402
from sesli_asistan.gateway.core.phrases import ablative, contact_dative, question_particle  # noqa: E402
from sesli_asistan.gateway.core.speech import LINE_RATE, prepare_for_tts, to_line  # noqa: E402

_ACRONYM = re.compile(r"\b[A-ZÇĞİÖŞÜ]{2,}\b")
_FOREIGN = re.compile(r"[qwxQWX]")
_DIGIT = re.compile(r"\d")


def flags(spoken: str, original: str) -> list[str]:
    """TTS'e giden metinde okunuşu belirsiz parçalar."""
    out = [f"kısaltma:{m}" for m in _ACRONYM.findall(original)]
    out += [f"noktalı:{m}" for m in re.findall(r"\b[\wçğıöşüÇĞİÖŞÜ]+\.(?=\s+[\wçğıöşüÇĞİÖŞÜ])", original)
            if len(m) <= 6]
    out += [f"yabancı harf:{w}" for w in spoken.split() if _FOREIGN.search(w)]
    if _DIGIT.search(spoken):
        out.append("rakam kaldı")
    return out


def sentences(rehberler: list[Path], cfg: DialogConfig) -> list[tuple[str, str]]:
    """(tür, cümle) listesi; tekrarlar atılır."""
    t = cfg.texts
    out: dict[str, str] = {}
    for k, v in fixed_prompts(cfg).items():
        if v:
            out.setdefault(v, f"anons:{k}")
    for path in rehberler:
        data = json.loads(path.read_text(encoding="utf-8"))
        contacts = [Contact.from_api(c) for c in data.get("contacts", [])]
        units: set[str] = set()
        titles: set[str] = set()
        for c in contacts:
            if c.deleted:
                continue
            if c.department:
                units.add(c.department)
            if c.kind == "department":
                out.setdefault(t.transferring_department.format(birim=c.department), f"birim:{path.stem}")
                continue
            to = contact_dative(c)
            out.setdefault(t.transferring.format(ad=c.full_name, ad_e=to), f"kişi:{path.stem}")
            out.setdefault(t.confirm.format(ad=c.full_name, ad_e=to, mi=question_particle(to)), f"teyit:{path.stem}")
            if getattr(c, "title", ""):
                titles.add(c.title)
        for u in sorted(units):
            out.setdefault(t.ask_person_in_department.format(birimden=ablative(u)), f"birimden:{path.stem}")
        for ti in sorted(titles):
            out.setdefault(t.which_department.format(unvan=ti), f"unvan:{path.stem}")
    return [(kind, text) for text, kind in out.items()]


def write_wav(path: Path, pcm8k: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(LINE_RATE)
        w.writeframes(pcm8k.astype("<i2").tobytes())


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rehber", type=Path, action="append", required=True)
    ap.add_argument("--sherpa", default="models/turkish-stt-66m-v2", help="geri dinleme için STT modeli")
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--cikti", type=Path, default=Path("okunus"))
    ap.add_argument("--en-kotu", type=int, default=40, help="dinlemek için WAV'ı yazılacak en şüpheli cümle sayısı")
    ap.add_argument("--yalniz-metin", action="store_true", help="seslendirmeden yalnız metin denetimi")
    a = ap.parse_args(argv)

    s = GatewaySettings(llm_enabled=False, directory_path=str(a.rehber[0]))
    cfg = build_agent(s, build_directory(s)).cfg          # kurumun ayar dosyasındaki cümlelerle (gateway.env)
    items = sentences(a.rehber, cfg)
    a.cikti.mkdir(parents=True, exist_ok=True)
    print(f"{len(items)} cümle")

    rows = []
    for i, (kind, text) in enumerate(items):
        spoken = prepare_for_tts(text)
        rows.append({"no": i, "tur": kind, "metin": text, "tts_metni": spoken, "isaret": "; ".join(flags(spoken, text)),
                     "stt": "", "cer": None})

    if not a.yalniz_metin:
        import jiwer
        from wer import norm

        from sesli_asistan.gateway.adapters.tts.piper_http import PiperHttpTts
        from sesli_asistan.libs.audio.resample import resample
        from sesli_asistan.stt.sherpa_engine import SherpaEngine, SherpaParams

        tts = PiperHttpTts(s.tts_url, s.tts_path, s.tts_format, s.tts_voice, s.tts_length_scale, s.tts_timeout_s)
        stt = SherpaEngine(SherpaParams(model_dir=a.sherpa, threads=a.threads))
        lines: dict[int, np.ndarray] = {}
        try:
            for r in rows:
                audio = await tts.synthesize(r["tts_metni"])
                line = to_line(audio)
                lines[r["no"]] = line
                x16 = resample(line.astype(np.float32) / 32768.0, LINE_RATE, 16000)
                r["stt"] = stt.transcribe(x16).text
                ref, hyp = norm(r["tts_metni"]), norm(r["stt"])
                r["cer"] = round(float(jiwer.cer(ref, hyp)) if ref else 0.0, 3)
                if r["no"] % 25 == 0:
                    print(f"  {r['no']}/{len(rows)}")
        finally:
            await tts.aclose()
        rows.sort(key=lambda r: (-(r["cer"] or 0), r["no"]))
        for r in rows[: a.en_kotu]:
            write_wav(a.cikti / f"{r['no']:04d}.wav", lines[r["no"]])
        cers = [r["cer"] for r in rows if r["cer"] is not None]
        print(f"ortalama CER {np.mean(cers):.3f} · CER > 0,2: {sum(c > 0.2 for c in cers)} cümle")

    with (a.cikti / "okunus.tsv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter="\t")
        w.writeheader()
        w.writerows(rows)
    flagged = [r for r in rows if r["isaret"]]
    print(f"metin işareti olan: {len(flagged)}")
    for r in flagged[:30]:
        print(f"  [{r['isaret']}] {r['metin']}")
    print(f"rapor: {a.cikti / 'okunus.tsv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
