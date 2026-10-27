"""STT servisini santral gibi besler: WAV → 8 kHz G.711 (A-law) → 20 ms kareler → gRPC akışı.

Önce STT servisini başlatın (ayrı terminal):
    python -m sesli_asistan.stt
Sonra:
    python tools/stt/dene.py kayit.wav                         # gerçek zaman hızında
    python tools/stt/dene.py k1.wav k2.wav --ipucu "Ayşe Yılmaz" --ipucu Muhasebe
    python tools/stt/dene.py kayit.wav --tsv referans.tsv      # metin doğruluğu da (dosya<TAB>metin)

Ölçülen (tasarim.md 11.1): `cumle_sonu_final_sn` = konuşmanın bittiği ses anından FINAL'ın gelmesine kadar
geçen süre (VAD bekleme payı + Whisper). Gerçek zaman hızında gönderildiğinde anlamlıdır (--hiz 1).
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from sesli_asistan.gateway.adapters.stt.grpc_client import GrpcStt  # noqa: E402
from sesli_asistan.libs.audio import g711  # noqa: E402
from sesli_asistan.libs.audio.resample import resample  # noqa: E402

RATE, FRAME = 8000, 160


def load(path: str) -> bytes:
    import soundfile as sf

    x, sr = sf.read(path, dtype="float32", always_2d=True)
    return g711.encode(resample(x.mean(axis=1), sr, RATE), "pcma")


async def run_file(client: GrpcStt, path: str, hotwords: list[str], speed: float) -> dict:
    payload = load(path)
    st = await client.open(Path(path).stem, "pcma", hotwords)
    events: list[dict] = []
    t0 = time.perf_counter()

    async def reader():
        async for e in st.events():
            now = time.perf_counter() - t0
            row = {"tip": e.type, "t_sn": round(now, 3), "metin": e.text, "guven": round(e.confidence, 3),
                   "bas_ms": e.start_ms, "son_ms": e.end_ms}
            if e.type == "not_recognized":
                row["neden"] = e.reason
            if e.type == "final" and speed > 0:
                row["cumle_sonu_final_sn"] = round(now - e.end_ms / 1000 / speed, 3)
            events.append(row)
            print(f"  {now:6.2f} sn  {e.type:15} {e.text or e.reason}")

    task = asyncio.create_task(reader())
    for i in range(0, len(payload), FRAME):
        if speed > 0:
            delay = t0 + (i / FRAME) * 0.02 / speed - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)
        await st.send(payload[i:i + FRAME])
    await st.close()
    await task
    finals = [e for e in events if e["tip"] == "final"]
    return {"dosya": path, "ses_sn": round(len(payload) / RATE, 2), "olaylar": events,
            "metin": " ".join(e["metin"] for e in finals), "atilan_kare": st.dropped,
            "taninamadi": [e["neden"] for e in events if e["tip"] == "not_recognized"],
            "gecikmeler": [e["cumle_sonu_final_sn"] for e in finals if "cumle_sonu_final_sn" in e]}


async def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dosyalar", nargs="+")
    ap.add_argument("--hedef", default="127.0.0.1:50051")
    ap.add_argument("--ipucu", action="append", default=[], help="Whisper hotwords (rehber adları)")
    ap.add_argument("--rehber", default=None,
                    help="ipuçlarını Gateway'in yaptığı gibi bu rehberden üret (ör. data/rehber_ornek.json)")
    ap.add_argument("--hiz", type=float, default=1.0, help="1 = gerçek zaman, 0 = beklemeden")
    ap.add_argument("--tsv", default=None, help="dosya<TAB>metin: doğru metinlerle WER")
    ap.add_argument("--cikti", default="sonuc_stt.json")
    a = ap.parse_args(argv)

    if a.rehber:
        from sesli_asistan.gateway.core.models import Contact
        from sesli_asistan.gateway.core.search import DirectoryIndex

        rows = json.loads(Path(a.rehber).read_text(encoding="utf-8"))["contacts"]
        a.ipucu = a.ipucu + DirectoryIndex(Contact.from_api(c) for c in rows).hotwords()
        print(f"ipuçları ({len(a.ipucu)}): {a.ipucu}")
    client = GrpcStt(a.hedef, connect_timeout_s=5)
    if not await client.ready():
        print(f"STT servisi hazır değil: {a.hedef} (python -m sesli_asistan.stt çalışıyor mu, model ısındı mı?)")
        return 1
    results = []
    for f in a.dosyalar:
        print(f"{f}:")
        results.append(await run_file(client, f, a.ipucu, a.hiz))
    await client.aclose()

    lat = [x for r in results for x in r["gecikmeler"]]
    report = {"hedef": a.hedef, "hiz": a.hiz, "ipucu": a.ipucu, "dosyalar": results,
              "ozet": {"final_sayisi": sum(1 for r in results for e in r["olaylar"] if e["tip"] == "final"),
                       "cumle_sonu_final_sn_p50": round(statistics.median(lat), 3) if lat else None,
                       "cumle_sonu_final_sn_en_cok": round(max(lat), 3) if lat else None,
                       "atilan_kare": sum(r["atilan_kare"] for r in results),
                       "taninamadi": sum(len(r["taninamadi"]) for r in results)}}
    if a.tsv:
        import jiwer

        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from tests.eval.wer import norm

        with open(a.tsv, encoding="utf-8") as f:
            refs = {Path(r["dosya"]).stem: r["metin"] for r in csv.DictReader(f, delimiter="\t")}
        pairs = [(norm(refs[Path(r["dosya"]).stem]), norm(r["metin"]))
                 for r in results if Path(r["dosya"]).stem in refs]
        if pairs:
            report["ozet"]["WER"] = round(jiwer.wer([p[0] for p in pairs], [p[1] for p in pairs]) * 100, 2)
            report["ozet"]["n"] = len(pairs)
    Path(a.cikti).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["ozet"], ensure_ascii=False))
    print(f"ayrıntı: {a.cikti}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
