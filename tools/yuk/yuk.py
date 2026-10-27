"""Yük testi: Gateway'e aynı anda çok sayıda gerçek çağrı (REST + kesintisiz RTP) yapar, gecikme dağılımını ölçer
(tasarim.md 11.3, NFR-01/NFR-03).

Her sanal çağrı santralin yaptığını yapar (tools/cagri/cagri.py ile aynı telefon ucu): çağrıyı açar, karşılamanın
bitmesini bekler, WAV'ları "arayan" olarak söyler, asistanın ilk sesine kadar geçen süreyi ölçer. Gateway aktarma
isterse ortak santral sunucusu `transfer_result` (varsayılan answered) gönderir; sonunda DELETE.

Ölçülenler:
  - tur gecikmesi (arayanın sözü bitti → asistanın ilk ses paketi): p50 / p90 / p95 / en çok
  - karşılama gecikmesi (POST /v1/calls → ilk ses paketi)
  - reddedilen çağrı (503: kapasite dolu ya da Gateway hazır değil), hata, cevapsız tur
  - Gateway'in /v1/health'inde görülen en yüksek eşzamanlı çağrı

    python tools/yuk/yuk.py ayse.wav --cagri 40 --eszamanli 10 --aralik 0.5
    python tools/yuk/yuk.py ayse.wav satis.wav --cagri 60 --eszamanli 30 --cikti sonuc_yuk.json

Gateway `PBX_BASE_URL=http://127.0.0.1:8100` ile açılmalı (eylemler bu araca gelir). Ölçümü yorumlarken: aynı
makinede çalışan araç da CPU kullanır; GPU ve LLM'in eşzamanlı istek kapasitesi asıl sınırdır.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cagri"))
from cagri import RATE, Phone, load, wait_quiet  # noqa: E402


@dataclass
class LoadConfig:
    wavs: list[str]
    gateway: str = "http://127.0.0.1:8000"
    key: str = ""
    pbx_port: int = 8100
    calls: int = 20
    concurrency: int = 5
    interval_s: float = 0.5           # iki çağrının başlaması arası (rampa)
    wait_s: float = 15.0              # asistan cevabı için en çok bekleme
    transfer_result: str = "answered"


@dataclass
class CallResult:
    call_id: str
    status: str = "ok"                # ok | rejected | error
    detail: str = ""
    greeting_s: float | None = None
    turns: list[float | None] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    duration_s: float = 0.0


def pct(values: list[float], p: float) -> float | None:
    return round(float(np.percentile(values, p)), 3) if values else None


class Santral:
    """Ortak santral ucu: Gateway'in eylemlerini çağrı kimliğine göre dağıtır."""

    def __init__(self):
        self.queues: dict[str, asyncio.Queue] = {}

    def app(self):
        from fastapi import FastAPI

        app = FastAPI()

        @app.post("/v1/calls/{cid}/actions", status_code=202)
        async def action(cid: str, body: dict):
            q = self.queues.get(cid)
            if q is not None:
                q.put_nowait(body)
            return {}

        return app


async def one_call(cfg: LoadConfig, gw, santral: Santral, clips: list[tuple[bytes, float]], n: int) -> CallResult:
    call_id = f"yuk-{n:04d}-{secrets.token_hex(2)}"
    res = CallResult(call_id)
    q: asyncio.Queue = asyncio.Queue()
    santral.queues[call_id] = q
    loop = asyncio.get_running_loop()
    phone = Phone()
    transport, _ = await loop.create_datagram_endpoint(lambda: phone, local_addr=("127.0.0.1", 0))
    rtp_task = None
    t0 = time.monotonic()
    opened = False
    try:
        r = await gw.post("/v1/calls", json={"call_id": call_id, "caller": "05320000000", "called": "4440000",
                                             "codec": "pcma", "rtp_remote": {
                                                 "host": "127.0.0.1", "port": transport.get_extra_info("sockname")[1]}})
        if r.status_code == 503:
            res.status, res.detail = "rejected", r.json().get("error", {}).get("code", "503")
            return res
        if r.status_code != 201:
            res.status, res.detail = "error", f"POST {r.status_code}"
            return res
        opened = True
        phone.target = ("127.0.0.1", r.json()["rtp_port"])
        rtp_task = asyncio.create_task(phone.run())
        await wait_quiet(phone, 0.7, cfg.wait_s, since=t0)
        res.greeting_s = round(phone.received[0][0] - t0, 3) if phone.received else None
        for payload, speech_end in clips:
            if not q.empty():
                break
            t_send = time.monotonic()
            phone.out += payload
            t_end = t_send + speech_end
            n_before = len(phone.received)
            await wait_quiet(phone, 0.7, cfg.wait_s + len(payload) / RATE, since=t_end)
            first = next((t for t, _ in phone.received[n_before:] if t > t_end), None)
            res.turns.append(round(first - t_end, 3) if first else None)
            await asyncio.sleep(0.2)
        try:
            act = await asyncio.wait_for(q.get(), timeout=1.0)
        except TimeoutError:
            act = None
        while act is not None:
            res.actions.append(act["type"] + (f":{act['extension']}" if act.get("extension") else ""))
            if act["type"] == "transfer":
                await gw.post(f"/v1/calls/{call_id}/events", json={"type": "transfer_result",
                                                                    "status": cfg.transfer_result})
            act = q.get_nowait() if not q.empty() else None
    except Exception as e:      # ağ hatası, zaman aşımı: çağrı hata sayılır, test sürer
        res.status, res.detail = "error", f"{type(e).__name__}: {e}"
    finally:
        if opened:
            reason = "transferred" if any(a.startswith("transfer") for a in res.actions) else "hangup"
            try:
                await gw.request("DELETE", f"/v1/calls/{call_id}", json={"reason": reason})
            except Exception as e:
                res.status, res.detail = "error", f"DELETE: {e}"
        if rtp_task:
            rtp_task.cancel()
        transport.close()
        santral.queues.pop(call_id, None)
        res.duration_s = round(time.monotonic() - t0, 2)
    return res


async def run_load(cfg: LoadConfig, progress=print) -> dict:
    import httpx
    import uvicorn

    clips = [load(w) for w in cfg.wavs]
    santral = Santral()
    server = uvicorn.Server(uvicorn.Config(santral.app(), host="127.0.0.1", port=cfg.pbx_port, log_level="warning"))
    srv_task = asyncio.create_task(server.serve())
    while not server.started:
        if srv_task.done():
            raise SystemExit(f"santral portu açılamadı: {cfg.pbx_port}")
        await asyncio.sleep(0.02)
    headers = {"Authorization": f"Bearer {cfg.key}"} if cfg.key else {}
    gw = httpx.AsyncClient(base_url=cfg.gateway, headers=headers, timeout=10,
                           limits=httpx.Limits(max_connections=cfg.concurrency * 2 + 4))
    peak = {"active": 0}
    stop = asyncio.Event()

    async def watch():
        while not stop.is_set():
            try:
                h = (await gw.get("/v1/health")).json()
                peak["active"] = max(peak["active"], h.get("active_calls", 0))
            except Exception:
                pass
            await asyncio.sleep(0.5)

    sem = asyncio.Semaphore(cfg.concurrency)
    results: list[CallResult] = []

    async def worker(n: int):
        await asyncio.sleep(n * cfg.interval_s if n < cfg.concurrency else 0)
        async with sem:
            r = await one_call(cfg, gw, santral, clips, n)
            results.append(r)
            lat = ", ".join("–" if t is None else f"{t:.2f}" for t in r.turns)
            progress(f"{len(results):>4}/{cfg.calls} {r.call_id} {r.status:<8} karşılama {r.greeting_s} sn, "
                     f"tur [{lat}] sn {' '.join(r.actions)} {r.detail}")

    t0 = time.monotonic()
    watcher = asyncio.create_task(watch())
    try:
        await asyncio.gather(*(worker(n) for n in range(cfg.calls)))
    finally:
        stop.set()
        await watcher
        await gw.aclose()
        server.should_exit = True
        await srv_task
    return summarize(cfg, results, time.monotonic() - t0, peak["active"])


def summarize(cfg: LoadConfig, results: list[CallResult], wall_s: float, peak: int) -> dict:
    ok = [r for r in results if r.status == "ok"]
    turns = [t for r in ok for t in r.turns if t is not None]
    greet = [r.greeting_s for r in ok if r.greeting_s is not None]
    missing = sum(1 for r in ok for t in r.turns if t is None)
    return {
        "ayar": {"cagri": cfg.calls, "eszamanli": cfg.concurrency, "aralik_sn": cfg.interval_s,
                 "wav": [Path(w).name for w in cfg.wavs]},
        "sonuc": {"basarili": len(ok), "reddedilen": sum(r.status == "rejected" for r in results),
                  "hata": sum(r.status == "error" for r in results), "cevapsiz_tur": missing,
                  "en_yuksek_eszamanli": peak, "sure_sn": round(wall_s, 1),
                  "aktarma": sum(any(a.startswith("transfer") for a in r.actions) for r in ok)},
        "tur_gecikmesi_sn": {"adet": len(turns), "p50": pct(turns, 50), "p90": pct(turns, 90),
                             "p95": pct(turns, 95), "en_cok": max(turns) if turns else None},
        "karsilama_sn": {"p50": pct(greet, 50), "p95": pct(greet, 95)},
        "hatalar": sorted({r.detail for r in results if r.status != "ok" and r.detail})[:10],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("wavlar", nargs="+", help="her çağrıda arayanın sırayla söyleyeceği cümleler")
    ap.add_argument("--gateway", default="http://127.0.0.1:8000")
    ap.add_argument("--anahtar", default="", help="Gateway API_KEY")
    ap.add_argument("--santral-port", type=int, default=8100, help="eylemleri alacak port (PBX_BASE_URL)")
    ap.add_argument("--cagri", type=int, default=20, help="toplam çağrı")
    ap.add_argument("--eszamanli", type=int, default=5, help="aynı anda açık en çok çağrı")
    ap.add_argument("--aralik", type=float, default=0.5, help="ilk dalgada çağrılar arası sn (rampa)")
    ap.add_argument("--bekle", type=float, default=15.0)
    ap.add_argument("--aktarma-sonucu", default="answered", choices=["answered", "busy", "no_answer", "failed"])
    ap.add_argument("--cikti", default="sonuc_yuk.json")
    a = ap.parse_args(argv)
    cfg = LoadConfig(a.wavlar, a.gateway, a.anahtar, a.santral_port, a.cagri, a.eszamanli, a.aralik, a.bekle,
                     a.aktarma_sonucu)
    report = asyncio.run(run_load(cfg))
    Path(a.cikti).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("sonuc", "tur_gecikmesi_sn", "karsilama_sn")}, ensure_ascii=False))
    print(f"ayrıntı: {a.cikti}")
    return 0 if report["sonuc"]["hata"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
