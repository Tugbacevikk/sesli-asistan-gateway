"""Santral simülatörü: Gateway'e gerçek bir çağrı yapar ve uçtan uca gecikmeyi ölçer (tasarim.md 6, 11.1, 15).

Santralin yaptığı her şeyi yapar:
  - `POST /v1/calls` ile çağrıyı açar, Gateway'in verdiği porta **kesintisiz** RTP gönderir (konuşma yokken
    sessizlik, telefondaki gibi), gelen RTP'yi kaydeder.
  - Verilen WAV'ları sırayla "arayan" olarak söyler: önce asistanın konuşmasının bitmesini bekler.
  - Kendi küçük HTTP sunucusunda Gateway'in eylemlerini (`/v1/calls/{id}/actions`) alır; aktarma gelirse
    `--aktarma-sonucu` ile cevap verir (`/events`), sonunda `DELETE` gönderir.

Ölçülen (arayanın duyduğu): **tur gecikmesi** = arayanın sözünün bittiği an (WAV'daki son konuşma) → asistanın
ilk ses paketinin gelişi. Cümle sonu bekleme + STT + LLM + TTS + ağ hepsi içindedir (NFR-01 hedefi p95 ≤ 2 sn).

    python -m sesli_asistan.stt                       # ayrı terminal
    PBX_BASE_URL=http://127.0.0.1:8100 python -m sesli_asistan.gateway     # ayrı terminal
    python tools/cagri/cagri.py ayse.wav --cevap cevap.wav
    python tools/cagri/cagri.py ayse.wav hayir_satis.wav --aktarma-sonucu busy
"""
from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from sesli_asistan.libs.audio import g711  # noqa: E402
from sesli_asistan.libs.audio.resample import resample  # noqa: E402

RATE, N = 8000, 160


def load(path: str) -> tuple[bytes, float]:
    """WAV → 8 kHz G.711 A-law baytları + konuşmanın bittiği saniye (sondaki sessizlik hariç)."""
    import soundfile as sf

    x, sr = sf.read(path, dtype="float32", always_2d=True)
    x = resample(x.mean(axis=1), sr, RATE)
    loud = np.flatnonzero(np.abs(x) > 0.02)
    speech_end = (loud[-1] + 1) / RATE if loud.size else len(x) / RATE
    return g711.encode(x, "pcma"), speech_end


class Phone(asyncio.DatagramProtocol):
    """Santralin RTP ucu: her 20 ms'de bir paket gönderir (sırada ses yoksa sessizlik), geleni kaydeder."""

    def __init__(self):
        self.out = bytearray()
        self.silence = g711.encode(np.zeros(N, np.float32), "pcma")
        self.received: list[tuple[float, bytes]] = []
        self.transport = None
        self.seq, self.ts, self.ssrc = secrets.randbits(16), secrets.randbits(32), secrets.randbits(32)
        self.target: tuple[str, int] | None = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        if len(data) > 12:
            self.received.append((time.monotonic(), data[12:]))

    async def run(self):
        loop = asyncio.get_running_loop()
        t0, i = loop.time(), 0
        while True:
            delay = t0 + i * 0.02 - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            chunk = bytes(self.out[:N]) if self.out else self.silence
            del self.out[:N]
            if len(chunk) < N:
                chunk += self.silence[: N - len(chunk)]
            if self.target:
                hdr = bytes([0x80, 8]) + self.seq.to_bytes(2, "big") + self.ts.to_bytes(4, "big") \
                    + self.ssrc.to_bytes(4, "big")
                self.transport.sendto(hdr + chunk, self.target)
                self.seq, self.ts = (self.seq + 1) & 0xFFFF, (self.ts + N) & 0xFFFFFFFF
            i += 1

    def last_rx(self) -> float | None:
        return self.received[-1][0] if self.received else None


async def wait_quiet(phone: Phone, quiet_s: float, timeout_s: float, since: float) -> bool:
    """Asistan konuştuysa susmasını bekler: `since`'ten sonra gelen paket var ve `quiet_s` boyunca yeni paket yok."""
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        last = phone.last_rx()
        if last and last > since and time.monotonic() - last > quiet_s:
            return True
        await asyncio.sleep(0.02)
    return False


async def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("wavlar", nargs="+", help="arayanın sırayla söyleyeceği cümleler")
    ap.add_argument("--gateway", default="http://127.0.0.1:8000")
    ap.add_argument("--anahtar", default="", help="Gateway API_KEY")
    ap.add_argument("--santral-port", type=int, default=8100, help="eylemleri alacak HTTP portu (PBX_BASE_URL)")
    ap.add_argument("--aktarma-sonucu", default="answered", choices=["answered", "busy", "no_answer", "failed"])
    ap.add_argument("--bekle", type=float, default=15.0, help="asistan cevabı için en çok bekleme (sn)")
    ap.add_argument("--cevap", default=None, help="asistanın sesini WAV olarak kaydet")
    ap.add_argument("--cikti", default="sonuc_cagri.json")
    a = ap.parse_args(argv)

    import httpx
    import uvicorn
    from fastapi import FastAPI

    call_id = f"sim-{secrets.token_hex(3)}"
    actions: list[dict] = []
    action_seen = asyncio.Event()
    santral = FastAPI()

    @santral.post("/v1/calls/{cid}/actions", status_code=202)
    async def action(cid: str, body: dict):
        actions.append({"t": time.monotonic(), **body})
        print(f"  ← eylem: {body}")
        action_seen.set()
        return {}

    server = uvicorn.Server(uvicorn.Config(santral, host="127.0.0.1", port=a.santral_port, log_level="warning"))
    srv_task = asyncio.create_task(server.serve())
    loop = asyncio.get_running_loop()
    phone = Phone()
    transport, _ = await loop.create_datagram_endpoint(lambda: phone, local_addr=("127.0.0.1", 0))
    my_port = transport.get_extra_info("sockname")[1]
    headers = {"Authorization": f"Bearer {a.anahtar}"} if a.anahtar else {}
    gw = httpx.AsyncClient(base_url=a.gateway, headers=headers, timeout=10)
    report: dict = {"call_id": call_id, "turlar": [], "eylemler": actions}
    rtp_task = None
    try:
        h = await gw.get("/v1/health")
        print(f"Gateway sağlık: {h.status_code} {h.json()}")
        t_open = time.monotonic()
        r = await gw.post("/v1/calls", json={"call_id": call_id, "caller": "05320000000", "called": "4440000",
                                             "codec": "pcma", "rtp_remote": {"host": "127.0.0.1", "port": my_port}})
        if r.status_code != 201:
            print(f"çağrı açılamadı: {r.status_code} {r.text}")
            return 1
        phone.target = ("127.0.0.1", r.json()["rtp_port"])
        rtp_task = asyncio.create_task(phone.run())
        print(f"çağrı {call_id}: RTP portu {phone.target[1]}")
        ok = await wait_quiet(phone, 0.7, a.bekle, since=t_open)
        report["karsilama_ilk_ses_sn"] = round(phone.received[0][0] - t_open, 3) if phone.received else None  # POST'tan
        print(f"karşılama: ilk ses {report['karsilama_ilk_ses_sn']} sn" + ("" if ok else " (bitmedi!)"))

        for wav in a.wavlar:
            if action_seen.is_set():
                break
            payload, speech_end = load(wav)
            t_send = time.monotonic()
            phone.out += payload
            t_end = t_send + speech_end
            n_before = len(phone.received)
            ok = await wait_quiet(phone, 0.7, a.bekle + len(payload) / RATE, since=t_end)
            first = next((t for t, _ in phone.received[n_before:] if t > t_end), None)
            turn = {"wav": wav, "konusma_sn": round(speech_end, 2),
                    "tur_gecikmesi_sn": round(first - t_end, 3) if first else None, "cevap_bitti": ok}
            report["turlar"].append(turn)
            print(f"{wav}: konuşma {speech_end:.2f} sn → asistanın ilk sesi {turn['tur_gecikmesi_sn']} sn")
            await asyncio.sleep(0.3)

        if actions and actions[-1]["type"] == "transfer":
            await asyncio.sleep(0.5)
            r = await gw.post(f"/v1/calls/{call_id}/events", json={"type": "transfer_result",
                                                                    "status": a.aktarma_sonucu})
            print(f"aktarma sonucu gönderildi: {a.aktarma_sonucu} → {r.status_code}")
            if a.aktarma_sonucu != "answered":
                since = time.monotonic()
                await wait_quiet(phone, 0.7, a.bekle, since=since)
                first = next((t for t, _ in phone.received if t > since), None)
                report["santral_olayi_sonrasi_ilk_ses_sn"] = round(first - since, 3) if first else None
    finally:
        await gw.request("DELETE", f"/v1/calls/{call_id}",
                         json={"reason": "transferred" if a.aktarma_sonucu == "answered" and actions else "hangup"})
        await gw.aclose()
        if rtp_task:
            rtp_task.cancel()
        transport.close()
        server.should_exit = True
        await srv_task

    lat = [t["tur_gecikmesi_sn"] for t in report["turlar"] if t["tur_gecikmesi_sn"] is not None]
    report["ozet"] = {"tur": len(report["turlar"]), "tur_gecikmesi_p50": float(np.median(lat)) if lat else None,
                      "tur_gecikmesi_en_cok": max(lat) if lat else None,
                      "eylem": [x["type"] + (f":{x['extension']}" if "extension" in x else "") for x in actions],
                      "alinan_paket": len(phone.received)}
    if a.cevap and phone.received:
        import soundfile as sf

        audio = g711.decode(b"".join(p for _, p in phone.received), "pcma")
        sf.write(a.cevap, audio, RATE)
        print(f"asistanın sesi: {a.cevap} ({len(audio) / RATE:.1f} sn)")
    Path(a.cikti).write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report["ozet"], ensure_ascii=False))
    print(f"ayrıntı: {a.cikti}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
