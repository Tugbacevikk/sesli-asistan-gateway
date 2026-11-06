"""Diyalog değerlendirmesi ve model karşılaştırması (tasarim.md 15.2, 15.3, ADR-005).

Diyalog setini gerçek LLM ile çalıştırır; her model için görev başarısını, izinsiz aktarmayı ve
tur gecikmesini raporlar. `qwen3.5:9b` / `qwen3.5:4b` seçimi bu raporla yapılır: NFR-01 (p95 ≤ 2 sn,
burada yalnızca LLM payı ölçülür) ve NFR-05'i sağlayan en küçük model seçilir; NFR-06 (izinsiz aktarma
= 0) her modelde zorunludur.

    python tests/eval/diyalog.py --model qwen3.5:9b --model qwen3.5:4b
    python tests/eval/diyalog.py --model qwen3.5:9b --tekrar 3 --sadece D10,D24
    python tests/eval/diyalog.py --kural            # LLM'siz (kural modu, ADR-019): Ollama gerekmez, kod yolları
    python tests/eval/diyalog.py --araclar mcp      # LLM'in rehber araması MCP sunucusunda (ADR-022; önce
                                                    #   python -m sesli_asistan.gateway.rehber_mcp)
    python tests/eval/diyalog.py --mcp-adimi        # akış rehber → MCP (konu) → LLM (ADR-023; sunucu gerekir)
    python tests/eval/diyalog.py --birim-listesi 40 --ornekler   # istem varyantı (ADR-024)
    python tests/eval/diyalog.py --istem istem.txt  # kendi istem şablonunuz ({kurum})

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

FAILED_EVENTS = {"busy", "no_answer", "failed"}


def evaluate(expected: dict[str, Any], steps: list[tuple[str, list]],
             allowed: set[str] | None = None) -> tuple[bool, list[str], list[str]]:
    """Senaryo sonucu: (başarılı mı, izinsiz aktarılan dahililer, yanlış aktarılan dahililer).

    steps: [(adım türü, o adımda üretilen eylemler)]; adım türü "arayan" ya da "santral:<durum>".
    - Santral bir aktarmanın başarısız olduğunu bildirdiyse (meşgul / cevapsız) o aktarma **denenmiş**
      sayılır: hata değildir, sonuç değerlendirmesine girmez (ör. D24: önce 214 denenir, meşgul, sonra 210).
    - izinsiz: hiçbir koşulda aktarılmaması gereken dahili (yasak_dahili) ya da rehberde aktarılabilir
      olmayan bir dahili (NFR-06). Güvenlik ölçütüdür; 0 olmalıdır.
    - yanlış: aktarılabilir ama beklenmeyen dahili (ör. "satış müdürü" yerine satış birim hattı). Doğruluk ölçütüdür.
    """
    forbidden = set(expected.get("yasak_dahili") or [])
    final: list = []
    every_transfer: list[str] = []
    for kind, acts in steps:
        if kind.startswith("santral:") and kind.split(":", 1)[1] in FAILED_EVENTS:
            final = [a for a in final if a.type != "transfer"]   # denenen aktarma düşer
        final += acts
        every_transfer += [a.extension for a in acts if a.type == "transfer"]
    izinsiz = sorted({t for t in every_transfer if t in forbidden or (allowed is not None and t not in allowed)})
    transfers = [a.extension for a in final if a.type == "transfer"]
    operator = any(a.type == "operator" for a in final)
    want = expected["eylem"]
    if want == "transfer":
        wanted = str(expected["dahili"])
        ok = bool(transfers) and transfers[-1] == wanted and not operator
        wrong = [t for t in transfers if t != wanted]
    elif want == "operator":
        ok, wrong = operator and not transfers, transfers
    elif want == "aktarma_yok":   # güvenli sonuç: aktarma yok (operatör ya da soru kabul)
        ok, wrong = not transfers, transfers
    elif want == "kapat":         # arayan bitirmek istedi: vedalaşıp kapatma, aktarma yok
        ok, wrong = any(a.type == "hangup" for a in final) and not transfers and not operator, transfers
    else:                         # yok: henüz eylem olmamalı
        ok, wrong = not transfers and not operator, transfers
    ok = ok and not izinsiz
    return ok, izinsiz, sorted(set(wrong) - set(izinsiz))


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


async def run_scenario(agent, sc: dict[str, Any], allowed: set[str] | None = None) -> dict[str, Any]:
    session = agent.new_session(sc["id"])
    session.greeting()
    actions, latencies, replies, llm_calls, steps = [], [], [], [], []
    convo = sc["konusma"]
    for i, step in enumerate(convo):
        if i and steps[-1][1] and any(a.type in ("transfer", "operator") for a in steps[-1][1]) and not (
                isinstance(step, dict) and "santral" in step):
            # aktarma / operatör eylemi çağrıyı santrale verdi: gerçek çağrıda arayanın sonraki sözü asistana
            # ulaşmaz (ölçüm 06.11.2026 K19: aktarmadan sonraki "evet" LLM'e gidip operatör sayılıyordu)
            break
        before = agent.llm.calls
        t = time.perf_counter()
        if isinstance(step, dict) and "santral" in step:
            turn = await session.handle_transfer_result(step["santral"])
        elif isinstance(step, dict):              # {arayan: metin, guven: STT güveni} (teyit / tekrar eşikleri)
            session.stt_confidence = step.get("guven")
            turn = await session.handle_user(step["arayan"])
            session.stt_confidence = None
        else:
            turn = await session.handle_user(step)
        latencies.append(time.perf_counter() - t)
        llm_calls.append(agent.llm.calls - before)
        actions += turn.actions
        steps.append((f"santral:{step['santral']}" if isinstance(step, dict) and "santral" in step else "arayan",
                      list(turn.actions)))
        replies.append(turn.text)
        if turn.ended:
            break
    ok, bad, wrong = evaluate(sc["beklenen"], steps, allowed)
    # yasak_metin: cevapta geçmemesi gerekenler (istem sızıntısı, dahili numara okuma; ADR-024)
    leaked = [x for x in sc["beklenen"].get("yasak_metin") or [] if any(x.casefold() in r.casefold() for r in replies)]
    ok = ok and not leaked
    return {"id": sc["id"], "ok": ok, "izinsiz": bad, "yanlis": wrong, "gecikme": latencies, "llm_cagri": llm_calls,
            "cevaplar": replies, "sizinti": leaked,
            "eylemler": [f"{a.type}:{getattr(a, 'extension', '')}" for a in actions]}


def _all(directory):
    """Aranabilir (silinmemiş, aktarılabilir) bütün kayıtlar."""
    from sesli_asistan.gateway.core.models import Candidate

    return [Candidate(c, 100) for c, _, _ in directory.index._entries]


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, round(p / 100 * (len(s) - 1)))]


async def run_model(model: str, scenarios: list[dict], repeat: int, timeout_s: float | None,
                    rules: bool = False, tools: str = "yerel", mcp_url: str = "",
                    mcp_step: bool = False, prompt: str = "", departments: int = 0,
                    few_shot: bool = False, rehber: str = "") -> dict[str, Any]:
    s = GatewaySettings()
    s.llm_model = model
    s.llm_enabled = not rules
    s.llm_tools = tools  # type: ignore[assignment]
    s.mcp_step = mcp_step
    s.llm_prompt_path, s.llm_prompt_departments, s.llm_few_shot = prompt, departments, few_shot
    if rehber:
        s.directory_path = rehber
    if mcp_url:
        s.mcp_url = mcp_url
    if timeout_s:
        s.llm_timeout_s = timeout_s
    directory = build_directory(s)
    await directory.refresh()
    agent = build_agent(s, directory)
    from sesli_asistan.gateway.composition import build_llm

    agent.llm = CountingLlm(build_llm(s))
    remote = agent.tools.remote
    if remote is not None and not await remote.ping():
        # ölçüm sessizce yerel aramaya düşmesin: iki yolu karşılaştırıyoruz
        raise SystemExit(f"MCP sunucusuna ulaşılamadı ({s.mcp_url}): python -m sesli_asistan.gateway.rehber_mcp")
    tool_ms = _time_in_contacts(agent.tools)
    if not rules:
        await agent.llm.chat([{"role": "user", "content": "merhaba"}], [])  # ısınma (ölçüme girmez)
    agent.llm.calls, agent.llm.times = 0, []
    # rehberde aktarılabilir dahililer: bunların dışına aktarma "izinsiz"dir
    allowed = {c.contact.extension for c in [*_all(directory)]}
    results = []
    for _ in range(repeat):
        for sc in scenarios:
            results.append(await run_scenario(agent, sc, allowed))
    if remote is not None:
        await remote.aclose()
    lat = [x for r in results for x in r["gecikme"]]
    turns = [c for r in results for c in r["llm_cagri"]]
    return {
        "model": model, "senaryo": len(scenarios), "tekrar": repeat, "llm_zaman_asimi_sn": s.llm_timeout_s,
        "llm_hatasi_turu": sum(1 for r in results for c in r["cevaplar"] if c == agent.cfg.texts.error),
        "tur_basina_llm_cagrisi": round(sum(turns) / len(turns), 2) if turns else 0,
        "llm_cagri_p50_sn": round(statistics.median(agent.llm.times), 2) if agent.llm.times else None,
        "basari_orani": round(100 * sum(r["ok"] for r in results) / len(results), 1),
        "izinsiz_aktarma": sum(len(r["izinsiz"]) for r in results),
        "yanlis_aktarma": sum(len(r["yanlis"]) for r in results),
        "tur_gecikmesi_p50_sn": round(statistics.median(lat), 2) if lat else None,
        "tur_gecikmesi_p95_sn": round(pct(lat, 95), 2),
        "araclar": tools, "rehber_aramasi": len(tool_ms),
        "rehber_aramasi_p50_ms": round(statistics.median(tool_ms), 2) if tool_ms else None,
        "rehber_aramasi_p95_ms": round(pct(tool_ms, 95), 2) if tool_ms else None,
        "mcp_hatasi": agent.tools.remote_failures,
        "istem_karakter": len(agent.system_prompt), "sizinti": sum(len(r["sizinti"]) for r in results),
        "mcp_adimi": mcp_step, "konu_sorgusu": agent.tools.topic_calls, "konu_bulundu": agent.tools.topic_hits,
        "konu_hatasi": agent.tools.topic_failures,
        "basarisizlar": sorted({r["id"] for r in results if not r["ok"]}),
        "ayrinti": results,
    }


def _time_in_contacts(tools: Any) -> list[float]:
    """LLM'in rehber aramalarının (InContacts) süresi, ms: yerel ve MCP yolu aynı yerden ölçülür."""
    times: list[float] = []
    inner = tools.execute_async

    async def timed(call, state):
        t0 = time.perf_counter()
        try:
            return await inner(call, state)
        finally:
            if call.name == "InContacts":
                times.append((time.perf_counter() - t0) * 1000)

    tools.execute_async = timed
    return times


async def main(argv=None) -> int:
    import yaml

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", default=str(ROOT / "tests/data/diyalog_seti.yaml"))
    ap.add_argument("--rehber", default="",
                    help="rehber dosyası (varsayılan: DIRECTORY_PATH); ör. tests/data/kamu_rehber.json")
    ap.add_argument("--model", action="append", help="birden çok kez verilebilir")
    ap.add_argument("--tekrar", type=int, default=1)
    ap.add_argument("--sadece", default="", help="virgülle senaryo kimlikleri")
    ap.add_argument("--zaman-asimi", type=float, default=None,
                    help="LLM zaman aşımı (sn). Doğruluk ölçümünde yüksek verin (ör. 60): yavaş donanımda "
                         "zaman aşımı, modelin doğruluğunu gizler. Gecikme ölçümünde varsayılan (5) kalmalı.")
    ap.add_argument("--cikti", default="sonuc_diyalog.json")
    ap.add_argument("--kural", action="store_true", help="LLM'siz (kural modu): Ollama gerekmez")
    ap.add_argument("--araclar", choices=["yerel", "mcp"], default="yerel",
                    help="LLM'in rehber araması: yerel (Gateway içinde) ya da mcp (rehber MCP sunucusu, ADR-022)")
    ap.add_argument("--mcp-url", default="", help="varsayılan: MCP_URL (http://127.0.0.1:8765/mcp)")
    ap.add_argument("--istem", default="", help="sistem istemi şablonu dosyası ({kurum}); boş: koddaki istem")
    ap.add_argument("--birim-listesi", type=int, default=0, metavar="N",
                    help="isteme kurumun birim listesi (en çok N birim); 0: yok")
    ap.add_argument("--ornekler", action="store_true", help="isteme örnek konuşmalar (few-shot)")
    ap.add_argument("--mcp-adimi", action="store_true",
                    help="akış rehber → MCP (konu_ara) → LLM (ADR-023); MCP sunucusu çalışıyor olmalı")
    ap.add_argument("-v", action="store_true", help="başarısız senaryoların konuşmasını yaz")
    a = ap.parse_args(argv)

    scenarios = yaml.safe_load(Path(a.set).read_text(encoding="utf-8"))
    if a.sadece:
        keep = set(a.sadece.split(","))
        scenarios = [s for s in scenarios if s["id"] in keep]
    models = ["kural"] if a.kural else (a.model or [GatewaySettings().llm_model])
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                                text=True, check=False).stdout.strip()
    except OSError:
        commit = ""
    report = {"commit": commit, "set": a.set, "araclar": a.araclar, "mcp_adimi": a.mcp_adimi, "sonuclar": []}
    print(f"araçlar: {a.araclar}   akış: {'rehber → MCP → LLM' if a.mcp_adimi else 'rehber → LLM'}   "
          f"istem: {a.istem or 'kod'}, birim listesi {a.birim_listesi or 'yok'}, "
          f"örnekler {'var' if a.ornekler else 'yok'}")
    report.update(istem=a.istem, birim_listesi=a.birim_listesi, ornekler=a.ornekler)
    print(f"{'model':16} {'başarı %':>9} {'izinsiz':>8} {'yanlış':>7} {'LLM hatası':>11} {'LLM/tur':>8} "
          f"{'p50 sn':>7} {'p95 sn':>7} {'arama':>6} {'ara p50 ms':>11} {'ara p95 ms':>11} {'MCP hata':>9}  "
          f"başarısızlar")
    for m in models:
        r = await run_model(m, scenarios, a.tekrar, a.zaman_asimi, rules=a.kural, tools=a.araclar,
                            mcp_url=a.mcp_url, mcp_step=a.mcp_adimi, prompt=a.istem,
                            departments=a.birim_listesi, few_shot=a.ornekler, rehber=a.rehber)
        report["sonuclar"].append(r)
        print(f"{m:16} {r['basari_orani']:>9} {r['izinsiz_aktarma']:>8} {r['yanlis_aktarma']:>7} "
              f"{r['llm_hatasi_turu']:>11} "
              f"{r['tur_basina_llm_cagrisi']:>8} {r['tur_gecikmesi_p50_sn']:>7} {r['tur_gecikmesi_p95_sn']:>7} "
              f"{r['rehber_aramasi']:>6} {r['rehber_aramasi_p50_ms']!s:>11} {r['rehber_aramasi_p95_ms']!s:>11} "
              f"{r['mcp_hatasi']:>9}  {','.join(r['basarisizlar'])}")
        print(f"{'':16} istem {r['istem_karakter']} karakter, sızıntı {r['sizinti']}")
        if a.mcp_adimi:
            print(f"{'':16} konu adımı: {r['konu_sorgusu']} sorgu, {r['konu_bulundu']} bulundu, "
                  f"{r['konu_hatasi']} hata")
        if a.v:
            by_id = {sc["id"]: sc for sc in scenarios}
            for d in r["ayrinti"]:
                if not d["ok"]:
                    sc = by_id[d["id"]]
                    print(f"\n  {d['id']} ({sc.get('aciklama', '')}) beklenen {sc['beklenen']}")
                    for said, reply in zip(sc["konusma"], d["cevaplar"], strict=False):
                        print(f"    arayan: {said}\n    asistan: {reply}")
                    print(f"    eylemler: {d['eylemler']}")
    Path(a.cikti).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nayrıntı: {a.cikti}  (ölçüm kaydına donanımı ve n'i ekleyin: tasarim.md 15.2)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
