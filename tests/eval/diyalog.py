"""Diyalog değerlendirmesi ve model karşılaştırması (tasarim.md 15.2, 15.3, ADR-005).

Diyalog setini gerçek LLM ile çalıştırır; her model için görev başarısını, izinsiz aktarmayı ve
tur gecikmesini raporlar. `qwen3.5:9b` / `qwen3.5:4b` seçimi bu raporla yapılır: NFR-01 (p95 ≤ 2 sn,
burada yalnızca LLM payı ölçülür) ve NFR-05'i sağlayan en küçük model seçilir; NFR-06 (izinsiz aktarma
= 0) her modelde zorunludur.

    python tests/eval/diyalog.py --model qwen3.5:9b --model qwen3.5:4b
    python tests/eval/diyalog.py --model qwen3.5:9b --tekrar 3 --sadece D10,D24

Ön koşul: Ollama çalışıyor ve modeller çekilmiş (`ollama pull qwen3.5:9b`).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from sesli_asistan.gateway.composition import build_agent, build_directory  # noqa: E402
from sesli_asistan.gateway.config import GatewaySettings  # noqa: E402


def evaluate(expected: dict[str, Any], actions: list) -> tuple[bool, list[str]]:
    """Senaryo sonucu: (başarılı mı, izinsiz aktarılan dahililer)."""
    transfers = [a.extension for a in actions if a.type == "transfer"]
    operator = any(a.type == "operator" for a in actions)
    forbidden = set(expected.get("yasak_dahili") or [])
    want = expected["eylem"]
    if want == "aktarma_yok":  # güvenli sonuç: hiç aktarma yok (operatör ya da soru sorulması kabul)
        return not transfers, sorted(set(transfers))
    if want == "transfer":
        wanted = str(expected["dahili"])
        bad = [t for t in transfers if t != wanted]
        ok = bool(transfers) and transfers[-1] == wanted and not operator
    elif want == "operator":
        bad = transfers
        ok = operator and not transfers
    else:  # yok
        bad = transfers
        ok = not transfers and not operator
    bad = sorted(set(bad) | (set(transfers) & forbidden))
    return ok, bad


class CountingLlm:
    """LLM çağrılarını sayar ve süresini ölçer (tur başına kaç LLM çağrısı yapıldığı gecikmeyi belirler)."""

    def __init__(self, inner):
        self.inner, self.calls, self.times = inner, 0, []

    async def chat(self, messages, tools):
        self.calls += 1
        t = time.perf_counter()
        try:
            return await self.inner.chat(messages, tools)
        finally:
            self.times.append(time.perf_counter() - t)


async def run_scenario(agent, sc: dict[str, Any]) -> dict[str, Any]:
    session = agent.new_session(sc["id"])
    session.greeting()
    actions, latencies, replies, llm_calls = [], [], [], []
    for step in sc["konusma"]:
        before = agent.llm.calls
        t = time.perf_counter()
        if isinstance(step, dict):
            turn = await session.handle_transfer_result(step["santral"])
        else:
            turn = await session.handle_user(step)
        latencies.append(time.perf_counter() - t)
        llm_calls.append(agent.llm.calls - before)
        actions += turn.actions
        replies.append(turn.text)
        if turn.ended:
            break
    ok, bad = evaluate(sc["beklenen"], actions)
    return {"id": sc["id"], "ok": ok, "izinsiz": bad, "gecikme": latencies, "llm_cagri": llm_calls,
            "cevaplar": replies,
            "eylemler": [f"{a.type}:{getattr(a, 'extension', '')}" for a in actions]}


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, round(p / 100 * (len(s) - 1)))]


async def run_model(model: str, scenarios: list[dict], repeat: int, timeout_s: float | None) -> dict[str, Any]:
    s = GatewaySettings()
    s.llm_model = model
    if timeout_s:
        s.llm_timeout_s = timeout_s
    directory = build_directory(s)
    await directory.refresh()
    agent = build_agent(s, directory)
    from sesli_asistan.gateway.composition import build_llm

    agent.llm = CountingLlm(build_llm(s))
    await agent.llm.chat([{"role": "user", "content": "merhaba"}], [])  # ısınma (ölçüme girmez)
    agent.llm.calls, agent.llm.times = 0, []
    results = []
    for _ in range(repeat):
        for sc in scenarios:
            results.append(await run_scenario(agent, sc))
    lat = [x for r in results for x in r["gecikme"]]
    turns = [c for r in results for c in r["llm_cagri"]]
    return {
        "model": model, "senaryo": len(scenarios), "tekrar": repeat, "llm_zaman_asimi_sn": s.llm_timeout_s,
        "llm_hatasi_turu": sum(1 for r in results for c in r["cevaplar"] if c == agent.cfg.texts.error),
        "tur_basina_llm_cagrisi": round(sum(turns) / len(turns), 2) if turns else 0,
        "llm_cagri_p50_sn": round(statistics.median(agent.llm.times), 2) if agent.llm.times else None,
        "basari_orani": round(100 * sum(r["ok"] for r in results) / len(results), 1),
        "izinsiz_aktarma": sum(len(r["izinsiz"]) for r in results),
        "tur_gecikmesi_p50_sn": round(statistics.median(lat), 2) if lat else None,
        "tur_gecikmesi_p95_sn": round(pct(lat, 95), 2),
        "basarisizlar": sorted({r["id"] for r in results if not r["ok"]}),
        "ayrinti": results,
    }


async def main(argv=None) -> int:
    import yaml

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", default=str(ROOT / "tests/data/diyalog_seti.yaml"))
    ap.add_argument("--model", action="append", help="birden çok kez verilebilir")
    ap.add_argument("--tekrar", type=int, default=1)
    ap.add_argument("--sadece", default="", help="virgülle senaryo kimlikleri")
    ap.add_argument("--zaman-asimi", type=float, default=None,
                    help="LLM zaman aşımı (sn). Doğruluk ölçümünde yüksek verin (ör. 60): yavaş donanımda "
                         "zaman aşımı, modelin doğruluğunu gizler. Gecikme ölçümünde varsayılan (5) kalmalı.")
    ap.add_argument("--cikti", default="sonuc_diyalog.json")
    a = ap.parse_args(argv)

    scenarios = yaml.safe_load(Path(a.set).read_text(encoding="utf-8"))
    if a.sadece:
        keep = set(a.sadece.split(","))
        scenarios = [s for s in scenarios if s["id"] in keep]
    models = a.model or [GatewaySettings().llm_model]
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                                text=True, check=False).stdout.strip()
    except OSError:
        commit = ""
    report = {"commit": commit, "set": a.set, "sonuclar": []}
    print(f"{'model':16} {'başarı %':>9} {'izinsiz':>8} {'LLM hatası':>11} {'LLM/tur':>8} {'p50 sn':>7} "
          f"{'p95 sn':>7}  başarısızlar")
    for m in models:
        r = await run_model(m, scenarios, a.tekrar, a.zaman_asimi)
        report["sonuclar"].append(r)
        print(f"{m:16} {r['basari_orani']:>9} {r['izinsiz_aktarma']:>8} {r['llm_hatasi_turu']:>11} "
              f"{r['tur_basina_llm_cagrisi']:>8} {r['tur_gecikmesi_p50_sn']:>7} {r['tur_gecikmesi_p95_sn']:>7}  "
              f"{','.join(r['basarisizlar'])}")
    Path(a.cikti).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nayrıntı: {a.cikti}  (ölçüm kaydına donanımı ve n'i ekleyin: tasarim.md 15.2)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
