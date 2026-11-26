"""Anlama modelini test setiyle ölçer (Ollama, üretimdeki adaptörle: `OllamaChat.chat_json`).

    python tools/egitim/anlama_olc.py --model qwen3.5:4b --model gemma4:e4b --model gemma4-santral
    python tools/egitim/anlama_olc.py --model gemma4-santral --sinir 200 -v

Ölçütler (model başına):
- json %:     çıktı geçerli JSON ve niyet tanınıyor (Understanding.parse)
- niyet %:    niyet doğru
- tam %:      bütün alanlar etiketle aynı (büyük/küçük harf ve Türkçe harf farkı yok sayılır)
- uçtan uca %: modelin çıktısı gerçek diyalog ajanından geçince doğru dahiliye gidiyor / doğru soruyu soruyor
              (asıl ölçüt: alan yazımı farklı olsa da arayan doğru yere ulaşıyor mu)
- p50 / p95:  çağrı süresi, sn (ısınma çağrısı sayılmaz)
- sohbet %:   (v8, `--sohbet`) kısa sohbet test setinde cevap üretimdeki denetimden geçiyor (tek sıcak cümle, soru /
              aktarma / numara yok; geçmezse arayan kodun selamını duyar). Ayrıca selamın karşılığı doğru mu ("günaydın"
              → "Günaydın").
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from anlama_veri import REHBERLER, EtiketLlm, dogrula, yer_adlarini_degistir  # noqa: E402

from sesli_asistan.gateway.adapters.llm.ollama import OllamaChat  # noqa: E402
from sesli_asistan.gateway.core.dialog import DialogAgent, DialogConfig  # noqa: E402
from sesli_asistan.gateway.core.dialog.small_talk import clean_small_talk  # noqa: E402
from sesli_asistan.gateway.core.dialog.understand import Understanding  # noqa: E402
from sesli_asistan.gateway.core.models import Contact  # noqa: E402
from sesli_asistan.gateway.core.phrases import greeting_echo  # noqa: E402
from sesli_asistan.gateway.core.search import DirectoryIndex, fold_words  # noqa: E402
from sesli_asistan.gateway.core.tools import ToolRegistry  # noqa: E402


def _norm(d: dict) -> dict:
    return {k: " ".join(fold_words(str(v))) for k, v in d.items() if v not in ("", None, 0)}


def alan_farklari(pred: dict, gold: dict) -> list[str]:
    """Tam eşleşmeyen çıktıda hangi alan nasıl yanlış: "unvan:eksik", "birim:fazla", "ad:farklı"."""
    p, g = _norm(pred), _norm(gold)
    out = []
    for k in sorted(set(p) | set(g)):
        if k not in p:
            out.append(f"{k}:eksik")
        elif k not in g:
            out.append(f"{k}:fazla")
        elif p[k] != g[k]:
            out.append(f"{k}:farklı")
    return out


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", action="append", required=True)
    ap.add_argument("--test", action="append",
                    help="test dosyası (birden çok verilebilir); verilmezse veri/anlama_test*.jsonl hepsi "
                         "(demo + v9'dan itibaren görülmemiş kurum: belediye)")
    ap.add_argument("--url", default="http://127.0.0.1:11434")
    ap.add_argument("--sinir", type=int, default=0, help="ilk N örnek (0: hepsi)")
    ap.add_argument("--zaman-asimi", type=float, default=30.0)
    ap.add_argument("--cikti", default="sonuc_anlama.json")
    ap.add_argument("--sohbet", default=str(ROOT / "tools/egitim/veri/sohbet_test.jsonl"),
                    help="kısa sohbet test seti (v8); dosya yoksa atlanır")
    ap.add_argument("-v", action="store_true", help="yanlışları yaz")
    a = ap.parse_args(argv)

    a.test = a.test or [str(p) for p in sorted((ROOT / "tools/egitim/veri").glob("anlama_test*.jsonl"))]
    rows = [json.loads(x) for yol in a.test for x in Path(yol).read_text(encoding="utf-8").splitlines() if x.strip()]
    rows = [r for r in rows if r.get("tur") != "sohbet"]          # sohbet ayrı ölçülür (JSON değil)
    sohbet_yolu = Path(a.sohbet)
    sohbet_rows = ([json.loads(x) for x in sohbet_yolu.read_text(encoding="utf-8").splitlines() if x.strip()]
                   if sohbet_yolu.exists() else [])
    if a.sinir:
        rows = rows[:a.sinir]
    # uçtan uca ölçüt için her rehberin ajanı (LLM yerine modelin çıktısını dönen sahte LLM)
    agents: dict[str, tuple[DialogAgent, EtiketLlm]] = {}
    for adi in {r["rehber"] for r in rows}:
        data = json.loads((ROOT / REHBERLER[adi]).read_text(encoding="utf-8"))
        llm = EtiketLlm()
        contacts, _ = yer_adlarini_degistir([Contact.from_api(c) for c in data["contacts"]])   # veriyle aynı rehber
        agents[adi] = (DialogAgent(llm, ToolRegistry(DirectoryIndex(contacts)), DialogConfig(understand="llm")), llm)

    rapor = {"test": a.test, "n": len(rows), "sonuclar": []}
    print(f"{len(rows)} örnek\n{'model':22} {'json %':>7} {'niyet %':>8} {'tam %':>6} {'uçtan uca %':>12} "
          f"{'p50 sn':>7} {'p95 sn':>7}")
    for model in a.model:
        llm = OllamaChat(base_url=a.url, model=model, timeout_s=a.zaman_asimi, think=False)
        await llm.chat_json([{"role": "user", "content": "merhaba"}])             # ısınma: modeli belleğe al
        sayac, sureler, yanlislar = Counter(), [], []
        alan_hatasi: Counter[str] = Counter()                 # tam eşleşmeyenlerde alan bazında hata
        tur_alan: dict[str, Counter[str]] = defaultdict(Counter)
        tam_yanlislar: list[dict] = []
        tur_basari: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        rehber_basari: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])     # tam, uçtan uca, n
        for r in rows:
            msgs = r["messages"][:2]
            gold = json.loads(r["messages"][2]["content"])
            t = time.perf_counter()
            try:
                out, _ = await llm.chat_json(msgs)
            except Exception as e:                                                # noqa: BLE001
                out = f"HATA: {e}"
            sureler.append(time.perf_counter() - t)
            u = Understanding.parse(out)
            try:
                pred = json.loads(out)
            except ValueError:
                pred = {}
            ok_json = u is not None
            ok_niyet = ok_json and pred.get("niyet") == gold["niyet"]
            ok_tam = ok_json and _norm(pred) == _norm(gold)
            agent, fake = agents[r["rehber"]]
            ok_uc = await dogrula(agent, fake, msgs[1]["content"], out, r.get("dahili")) if ok_json else False
            if r.get("dahili") is None:
                ok_uc = ok_niyet                       # aktar dışı: niyet doğruysa kod doğru yolu seçer
            sayac.update(json=ok_json, niyet=ok_niyet, tam=ok_tam, uc=ok_uc)
            if ok_json and not ok_tam:
                fark = alan_farklari(pred, gold)
                alan_hatasi.update(fark)
                tur_alan[r["tur"]].update(fark)
                tam_yanlislar.append({"soz": msgs[1]["content"], "beklenen": gold, "cikti": pred, "tur": r["tur"],
                                      "fark": fark, "uc_dogru": bool(ok_uc)})
            tur_basari[r["tur"]][0] += ok_uc
            tur_basari[r["tur"]][1] += 1
            rb = rehber_basari[r["rehber"]]
            rb[0] += ok_tam
            rb[1] += ok_uc
            rb[2] += 1
            if not ok_uc:
                yanlislar.append({"soz": msgs[1]["content"], "beklenen": gold, "cikti": out, "tur": r["tur"]})
        await llm.aclose()
        n = len(rows)
        s = sorted(sureler)
        res = {"model": model, **{k: round(100 * sayac[k] / n, 1) for k in ("json", "niyet", "tam", "uc")},
               "p50_sn": round(statistics.median(s), 2), "p95_sn": round(s[min(n - 1, round(0.95 * (n - 1)))], 2),
               "tur_basina": {k: round(100 * v[0] / v[1], 1) for k, v in sorted(tur_basari.items())},
               "rehber_basina": {k: {"tam": round(100 * v[0] / v[2], 1), "uc": round(100 * v[1] / v[2], 1),
                                     "n": v[2]} for k, v in sorted(rehber_basari.items())},
               "yanlislar": yanlislar,
               "alan_hatasi": dict(alan_hatasi.most_common()),
               "tur_alan_hatasi": {k: dict(v.most_common(5)) for k, v in sorted(tur_alan.items())},
               "tam_yanlislar": tam_yanlislar}
        if sohbet_rows:
            res["sohbet"] = await sohbet_olc(llm_model=model, url=a.url, zaman=a.zaman_asimi, rows=sohbet_rows)
        rapor["sonuclar"].append(res)
        print(f"{model:22} {res['json']:>7} {res['niyet']:>8} {res['tam']:>6} {res['uc']:>12} "
              f"{res['p50_sn']:>7} {res['p95_sn']:>7}")
        if len(res["rehber_basina"]) > 1:      # demo ve görülmemiş kurum (belediye) ayrı
            print(f"{'':22} rehbere göre (tam % / uçtan uca %): " + ", ".join(
                f"{k} {v['tam']} / {v['uc']} (n {v['n']})" for k, v in res["rehber_basina"].items()))
        print(f"{'':22} türe göre uçtan uca: " + ", ".join(f"{k} {v}" for k, v in res["tur_basina"].items()))
        print(f"{'':22} tam eşleşmeyenlerde alan hatası (ilk 12): "
              + ", ".join(f"{k} {v}" for k, v in alan_hatasi.most_common(12)))
        if "sohbet" in res:
            so = res["sohbet"]
            print(f"{'':22} sohbet: denetimden geçen %{so['gecen']}, selam karşılığı doğru %{so['selam']}, "
                  f"p50 {so['p50_sn']} sn (n = {so['n']})")
            if a.v:
                for y in so["gecmeyenler"][:10]:
                    print(f"    [sohbet] {y['soz']}\n        çıktı    {y['cikti']}")
        if a.v:
            for y in yanlislar[:30]:
                print(f"    [{y['tur']}] {y['soz']}\n        beklenen {y['beklenen']}\n        çıktı    {y['cikti']}")
    Path(a.cikti).write_text(json.dumps(rapor, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nayrıntı: {a.cikti}")
    return 0


async def sohbet_olc(llm_model: str, url: str, zaman: float, rows: list[dict]) -> dict:
    """Kısa sohbet: üretimdeki gibi araçsız düz çağrı (`chat`), cevap `clean_small_talk` denetiminden geçiyor mu."""
    llm = OllamaChat(base_url=url, model=llm_model, timeout_s=zaman, think=False)
    gecen = selam = 0
    sureler, gecmeyenler = [], []
    for r in rows:
        msgs = r["messages"][:2]
        t = time.perf_counter()
        try:
            out = (await llm.chat(msgs, [])).content
        except Exception as e:                                                    # noqa: BLE001
            out = f"HATA: {e}"
        sureler.append(time.perf_counter() - t)
        temiz = clean_small_talk(out)
        if temiz is None:
            gecmeyenler.append({"soz": msgs[1]["content"], "cikti": out})
            continue
        gecen += 1
        echo = greeting_echo(msgs[1]["content"])
        selam += (echo is None) or temiz.startswith(echo)
    await llm.aclose()
    n, s = len(rows), sorted(sureler)
    return {"n": n, "gecen": round(100 * gecen / n, 1), "selam": round(100 * selam / n, 1),
            "p50_sn": round(statistics.median(s), 2), "gecmeyenler": gecmeyenler}


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
