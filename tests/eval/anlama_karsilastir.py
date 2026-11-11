"""Kural kipi ile "LLM gerektiğinde anlar" kipini (ADR-037) aynı senaryo setlerinde karşılaştırır.

Her set üç kipte koşar:
    kural   LLM hiç yok (LLM_ENABLED=false): yalnız kurallar
    normal  bugünkü kurulum: kurallar, çözemezse LLM (araçlı döngü)
    anlama  UNDERSTAND_MODE=llm: kuralların çözemediği söz LLM'le kalıba çevrilir, karar kodda

    python tests/eval/anlama_karsilastir.py                       # bütün setler, qwen3.5:4b
    python tests/eval/anlama_karsilastir.py --set serbest --set sirket --model qwen3.5:9b
    python tests/eval/anlama_karsilastir.py --kipler kural,anlama -v

Ön koşul: Ollama çalışıyor, model çekilmiş. "kural" kipi Ollama'sız da koşar. Sonuç tablosu ekrana, ayrıntı JSON'a
(`--cikti`); ölçüm kaydına donanımı ve commit'i ekleyin (tasarim.md 15.2). Karar ölçütü (ADR-037): anlama kipi
izinsiz aktarma 0, başarıda kural kipinden düşük değil, tur gecikmesi p95 ≤ 2 sn.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests/eval"))
import diyalog  # noqa: E402

SETS = {  # kısa ad → (set, rehber)
    "serbest": ("diyalog_seti_serbest.yaml", "data/sirket_rehber.json"),
    "sirket": ("diyalog_seti_sirket.yaml", "data/sirket_rehber.json"),
    "demo": ("diyalog_seti_demo.yaml", "data/demo_rehber.json"),
    "sentetik": ("diyalog_seti_sentetik.yaml", "data/sentetik_kurum_rehber.json"),
    "kamu": ("diyalog_seti_kamu.yaml", "tests/data/kamu_rehber.json"),
    "makam": ("diyalog_seti_makam.yaml", "tests/data/makam_rehber.json"),
    "genel": ("diyalog_seti.yaml", ""),
}
MODES = ("kural", "normal", "anlama")
_ID = re.compile(r"^\[([^\]]+)\] (anlama.*)$")


class UnderstandingLog(logging.Handler):
    """Anlama satırlarını ("[F01] anlama (LLM, 2130 ms): … → aktar unvan=müdür → 'muhasebe müdür'") senaryoya göre
    toplar: başarısız senaryoda LLM'in ne anladığı ve kalıbın ne olduğu görünsün."""

    def __init__(self):
        super().__init__(logging.INFO)
        self.lines: dict[str, list[str]] = {}

    def emit(self, record):
        if m := _ID.match(record.getMessage()):
            self.lines.setdefault(m.group(1), []).append(m.group(2))


async def main(argv=None) -> int:
    import yaml

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--set", action="append", choices=list(SETS), help="birden çok kez; boş: hepsi")
    ap.add_argument("--model", default="qwen3.5:4b")
    ap.add_argument("--kipler", default=",".join(MODES), help="virgülle: kural,normal,anlama")
    ap.add_argument("--cikti", default="sonuc_anlama.json")
    ap.add_argument("-v", action="store_true", help="anlama kipinde başarısız senaryoların konuşmasını yaz")
    a = ap.parse_args(argv)
    modes = [m for m in a.kipler.split(",") if m in MODES]
    names = a.set or list(SETS)
    seen = UnderstandingLog()
    dialog_log = logging.getLogger("sesli_asistan.gateway.core.dialog")
    dialog_log.addHandler(seen)
    dialog_log.setLevel(logging.INFO)       # yalnız bu toplayıcı için; ekrana yine yalnız uyarılar
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                            check=False).stdout.strip()
    report: dict = {"commit": commit, "model": a.model, "sonuclar": []}
    print(f"model {a.model}, commit {commit}\n")
    print(f"{'set':10} {'kip':7} {'n':>4} {'başarı %':>9} {'yanlış':>7} {'izinsiz':>8} {'LLM/tur':>8} "
          f"{'p50 sn':>7} {'p95 sn':>7}  başarısızlar")
    for name in names:
        file, rehber = SETS[name]
        scenarios = yaml.safe_load((ROOT / "tests/data" / file).read_text(encoding="utf-8"))
        for mode in modes:
            seen.lines.clear()
            r = await diyalog.run_model("kural" if mode == "kural" else a.model, scenarios, 1, None,
                                        rules=mode == "kural", understand=mode == "anlama",
                                        rehber=str(ROOT / rehber) if rehber else "")
            if mode == "anlama":
                r["anlama_satirlari"] = dict(seen.lines)
                r["llme_giden_soz"] = sum(len(v) for v in seen.lines.values())
            report["sonuclar"].append({"set": name, "kip": mode, **r})
            fails = ",".join(r["basarisizlar"])
            print(f"{name:10} {mode:7} {r['senaryo']:>4} {r['basari_orani']:>9} {r['yanlis_aktarma']:>7} "
                  f"{r['izinsiz_aktarma']:>8} {r['tur_basina_llm_cagrisi']:>8} {r['tur_gecikmesi_p50_sn']!s:>7} "
                  f"{r['tur_gecikmesi_p95_sn']!s:>7}  {fails[:60]}")
            if a.v and mode == "anlama":
                by_id = {sc["id"]: sc for sc in scenarios}
                for d in r["ayrinti"]:
                    if not d["ok"]:
                        sc = by_id[d["id"]]
                        print(f"    {d['id']} ({sc.get('aciklama', '')}) beklenen {sc['beklenen']}")
                        for said, reply in zip(sc["konusma"], d["cevaplar"], strict=False):
                            print(f"      arayan: {said}\n      asistan: {reply}")
                        print(f"      eylemler: {d['eylemler']}")
                        for line in seen.lines.get(d["id"], []):
                            print(f"      {line}")
            if mode == "anlama":
                print(f"{'':19}LLM'e giden söz: {r['llme_giden_soz']}")
        print()
    Path(a.cikti).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"ayrıntı: {a.cikti}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
