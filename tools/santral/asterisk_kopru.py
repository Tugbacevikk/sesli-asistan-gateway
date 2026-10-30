"""Asterisk ↔ Gateway köprüsü (ARI): gerçek bir santralle uçtan uca deneme (tasarim.md 6.2, 15.5).

Asterisk'te asistana yönlendirilen çağrı (dialplan: `Stasis(sesli-asistan)`) bu köprüye gelir. Köprü, Gateway'in
santral sözleşmesini (REST + RTP) Asterisk'in REST arayüzüyle (ARI) uygular:

  çağrı gelir      → Gateway'e POST /v1/calls → dönen RTP portuna Asterisk ExternalMedia kanalı (G.711 A-law, RTP)
                     arayan + ExternalMedia aynı köprüde (bridge): ses iki yönlü akar
  tuşlama          → POST /v1/calls/{id}/events {"type": "dtmf"}
  "214'e aktar"    → Asterisk 214'ü arar (arayana çalma sesi); açılırsa asistan köprüden çıkar, arayan ↔ 214
                     konuşur, Gateway'e "answered" + DELETE; meşgul / cevapsız / hata ise sonucu bildirir, arayan
                     asistanla devam eder
  "operatöre"      → operatör dahilisi aranır (aynı yol, sonuç beklenmez)
  "kapat"          → arayan kapatılır (mesai dışı anonsundan sonra)
  arayan kapattı   → DELETE /v1/calls/{id}

    python tools/santral/asterisk_kopru.py --ari http://127.0.0.1:8088 --ari-kullanici sesli --ari-sifre ... \\
        --gateway http://127.0.0.1:8000 --gateway-anahtar ... --rtp-host 127.0.0.1 --dinle 127.0.0.1:8100

Gateway'de: PBX_BASE_URL=http://127.0.0.1:8100 (bu köprü), PBX_ALLOWED_IPS=<Asterisk ve köprünün adresi>.
Asterisk ayarları: deploy/asterisk/. Bağımlılıklar Gateway'inkilerle aynı (httpx, websockets, fastapi, uvicorn).
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("kopru")

APP = "sesli-asistan"
# Q.850 kapanış nedenleri → Gateway aktarma sonucu
BUSY_CAUSES = {17, 21}            # meşgul, reddedildi
NO_ANSWER_CAUSES = {18, 19, 20}   # kullanıcı cevap vermedi / çaldı açılmadı / abone yok


class Ari(Protocol):
    async def answer(self, channel: str) -> None: ...
    async def create_bridge(self) -> str: ...
    async def add(self, bridge: str, *channels: str) -> None: ...
    async def remove(self, bridge: str, channel: str) -> None: ...
    async def destroy_bridge(self, bridge: str) -> None: ...
    async def external_media(self, host_port: str, codec: str) -> tuple[str, tuple[str, int] | None]: ...
    async def originate(self, endpoint: str, args: str, caller_id: str, timeout_s: int) -> str: ...
    async def hangup(self, channel: str) -> None: ...
    async def play(self, bridge: str, media: str) -> str: ...
    async def stop_playback(self, playback: str) -> None: ...


class Gateway(Protocol):
    async def create_call(self, call_id: str, caller: str, called: str, codec: str) -> int: ...
    async def event(self, call_id: str, body: dict[str, Any]) -> None: ...
    async def end(self, call_id: str, reason: str) -> None: ...


@dataclass
class Call:
    id: str                          # arayan kanalının kimliği = Gateway call_id
    caller: str
    bridge: str = ""
    media: str = ""                  # ExternalMedia kanalı
    target: str = ""                 # aranan dahilinin kanalı (aktarma sürerken)
    target_ext: str = ""
    target_kind: str = ""            # transfer | operator
    ring: str = ""                   # çalma sesi playback
    handed_off: bool = False         # asistan çağrıyı bıraktı (aktarma / operatör / kapatma)
    gw_open: bool = False            # Gateway'de oturum açık (DELETE gönderilmedi)
    ended: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


class Bridge:
    """Olay işleyici: ARI olayları ve Gateway eylemleri → santral işlemleri. Ağdan bağımsız (test edilebilir)."""

    def __init__(self, ari: Ari, gw: Gateway, rtp_host: str, operator_ext: str, endpoint_tech: str = "PJSIP",
                 ring_timeout_s: int = 25, codec: str = "pcma"):
        self.ari, self.gw = ari, gw
        self.rtp_host, self.operator_ext, self.tech = rtp_host, operator_ext, endpoint_tech
        self.ring_timeout_s, self.codec = ring_timeout_s, codec
        self.calls: dict[str, Call] = {}
        self.by_channel: dict[str, str] = {}       # yardımcı kanal (media / hedef) → çağrı

    # --- Asterisk olayları ------------------------------------------------------------------------------------

    async def on_event(self, ev: dict[str, Any]) -> None:
        kind = ev.get("type")
        ch = ev.get("channel") or {}
        cid = ch.get("id", "")
        try:
            if kind == "StasisStart":
                args = ev.get("args") or []
                if args[:1] == ["hedef"] and len(args) >= 2:
                    await self._target_answered(args[1], cid)
                elif cid not in self.by_channel and not ch.get("name", "").startswith("UnicastRTP/"):
                    await self._new_call(ch)
            elif kind == "ChannelDtmfReceived" and cid in self.calls:
                await self.gw.event(cid, {"type": "dtmf", "digit": ev.get("digit", "")})
            elif kind in ("StasisEnd", "ChannelDestroyed"):
                if cid in self.calls:
                    await self._caller_gone(cid)
                elif cid in self.by_channel:
                    await self._helper_gone(cid, int(ev.get("cause") or 0), kind)
        except Exception:
            log.exception("olay işlenemedi: %s %s", kind, cid)

    async def _new_call(self, ch: dict[str, Any]) -> None:
        cid = ch["id"]
        caller = (ch.get("caller") or {}).get("number", "")
        called = (ch.get("dialplan") or {}).get("exten", "")
        c = Call(cid, caller)
        self.calls[cid] = c
        log.info("[%s] yeni çağrı: %s → %s", cid, caller, called)
        try:
            await self.ari.answer(cid)
            port = await self.gw.create_call(cid, caller, called, self.codec)
            c.gw_open = True
        except GatewayBusy as e:
            # Gateway hazır değil / dolu: santral kuralı → operatöre (asistansız)
            log.warning("[%s] Gateway çağrıyı almadı (%s): operatöre", cid, e)
            c.handed_off = True
            await self._dial(c, self.operator_ext, "operator")
            return
        c.bridge = await self.ari.create_bridge()
        c.media, local = await self.ari.external_media(f"{self.rtp_host}:{port}", self.codec)
        self.by_channel[c.media] = cid
        if local:
            # Asterisk'in ses alma adresi: Gateway karşılamayı ilk paketi beklemeden göndersin
            await self.gw.event(cid, {"type": "rtp_remote", "rtp_remote": {"host": local[0], "port": local[1]}})
        await self.ari.add(c.bridge, cid, c.media)
        log.info("[%s] ses köprülendi: Gateway RTP %s:%d", cid, self.rtp_host, port)

    async def _caller_gone(self, cid: str) -> None:
        c = self.calls.pop(cid, None)
        if c is None or c.ended:
            return
        c.ended = True
        log.info("[%s] arayan kapattı", cid)
        await self._end_gw(c, "hangup")
        for ch in (c.media, c.target):
            if ch:
                self.by_channel.pop(ch, None)
                with contextlib.suppress(Exception):
                    await self.ari.hangup(ch)
        if c.bridge:
            with contextlib.suppress(Exception):
                await self.ari.destroy_bridge(c.bridge)

    async def _helper_gone(self, ch: str, cause: int, kind: str) -> None:
        cid = self.by_channel.pop(ch, None)
        c = self.calls.get(cid or "")
        if c is None:
            return
        if ch == c.target:
            c.target = ""
            if c.handed_off:                         # aktarılan konuşma bitti: arayanı da kapat
                with contextlib.suppress(Exception):
                    await self.ari.hangup(c.id)
                return
            await self._stop_ring(c)
            status = "busy" if cause in BUSY_CAUSES else "no_answer" if cause in NO_ANSWER_CAUSES else "failed"
            log.info("[%s] %s aranamadı (neden %d → %s)", cid, c.target_ext, cause, status)
            if c.target_kind == "operator":
                with contextlib.suppress(Exception):
                    await self.ari.hangup(c.id)      # operatöre ulaşılamadı: çağrı kapanır
            else:
                await self.gw.event(c.id, {"type": "transfer_result", "status": status})
        elif ch == c.media and not c.handed_off and kind == "ChannelDestroyed":
            log.warning("[%s] ses kanalı kapandı", cid)

    async def _target_answered(self, cid: str, target: str) -> None:
        c = self.calls.get(cid)
        if c is None:
            with contextlib.suppress(Exception):
                await self.ari.hangup(target)        # arayan bu arada kapatmış
            return
        await self._stop_ring(c)
        log.info("[%s] %s cevapladı: aktarılıyor", cid, c.target_ext)
        c.handed_off = True
        if c.media:
            self.by_channel.pop(c.media, None)
            with contextlib.suppress(Exception):
                await self.ari.remove(c.bridge, c.media)
                await self.ari.hangup(c.media)
            c.media = ""
        await self.ari.add(c.bridge, target)
        if c.target_kind == "transfer" and c.gw_open:
            await self.gw.event(c.id, {"type": "transfer_result", "status": "answered"})
        await self._end_gw(c, "transferred")

    # --- Gateway eylemleri ------------------------------------------------------------------------------------

    async def on_action(self, cid: str, action: dict[str, Any]) -> None:
        c = self.calls.get(cid)
        if c is None:
            log.warning("[%s] bilinmeyen çağrı için eylem: %s", cid, action)
            return
        kind = action.get("type")
        log.info("[%s] Gateway eylemi: %s", cid, action)
        if kind == "transfer":
            await self._dial(c, str(action.get("extension", "")), "transfer")
        elif kind == "operator":
            c.handed_off = True                      # Gateway sonucu beklemez
            await self._dial(c, self.operator_ext, "operator")
        elif kind == "hangup":
            await asyncio.sleep(0.3)                 # anonsun son paketi arayana ulaşsın
            await self._end_gw(c, "hangup")
            c.handed_off = True
            with contextlib.suppress(Exception):
                await self.ari.hangup(cid)

    async def _dial(self, c: Call, ext: str, kind: str) -> None:
        if not ext.isdigit():
            await self.gw.event(c.id, {"type": "transfer_result", "status": "failed"})
            return
        c.target_ext, c.target_kind = ext, kind
        if c.bridge:
            with contextlib.suppress(Exception):
                c.ring = await self.ari.play(c.bridge, "tone:ring;tonezone=tr")
        try:
            c.target = await self.ari.originate(f"{self.tech}/{ext}", f"hedef,{c.id}", c.caller, self.ring_timeout_s)
        except Exception as e:
            log.error("[%s] %s aranamadı: %s", c.id, ext, e)
            await self._stop_ring(c)
            if kind == "transfer":
                await self.gw.event(c.id, {"type": "transfer_result", "status": "failed"})
            return
        self.by_channel[c.target] = c.id
        if not c.bridge:                             # asistansız (Gateway reddetti): köprüyü şimdi kur
            c.bridge = await self.ari.create_bridge()
            await self.ari.add(c.bridge, c.id)

    async def _end_gw(self, c: Call, reason: str) -> None:
        if c.gw_open:
            c.gw_open = False
            await self.gw.end(c.id, reason)

    async def _stop_ring(self, c: Call) -> None:
        if c.ring:
            with contextlib.suppress(Exception):
                await self.ari.stop_playback(c.ring)
            c.ring = ""


class GatewayBusy(Exception):
    """Gateway çağrıyı almadı (503 / ulaşılamıyor)."""


# --- ağ uygulamaları ---------------------------------------------------------------------------------------------

class AriHttp:
    def __init__(self, base: str, user: str, password: str, app: str = APP):
        import httpx

        self.app = app
        self.http = httpx.AsyncClient(base_url=base.rstrip("/") + "/ari", auth=(user, password), timeout=10)

    async def _post(self, path: str, **params: Any) -> Any:
        r = await self.http.post(path, params={k: v for k, v in params.items() if v is not None})
        r.raise_for_status()
        return r.json() if r.content else None

    async def answer(self, channel: str) -> None:
        await self._post(f"/channels/{channel}/answer")

    async def create_bridge(self) -> str:
        return (await self._post("/bridges", type="mixing"))["id"]

    async def add(self, bridge: str, *channels: str) -> None:
        await self._post(f"/bridges/{bridge}/addChannel", channel=",".join(channels))

    async def remove(self, bridge: str, channel: str) -> None:
        await self._post(f"/bridges/{bridge}/removeChannel", channel=channel)

    async def destroy_bridge(self, bridge: str) -> None:
        await self.http.delete(f"/bridges/{bridge}")

    async def external_media(self, host_port: str, codec: str) -> str:
        fmt = {"pcma": "alaw", "pcmu": "ulaw"}[codec]
        ch = await self._post("/channels/externalMedia", app=self.app, external_host=host_port, format=fmt,
                              encapsulation="rtp", transport="udp", connection_type="client", direction="both")
        v = ch.get("channelvars") or {}
        local = None
        if v.get("UNICASTRTP_LOCAL_ADDRESS") and v.get("UNICASTRTP_LOCAL_PORT"):
            local = (v["UNICASTRTP_LOCAL_ADDRESS"], int(v["UNICASTRTP_LOCAL_PORT"]))
        return ch["id"], local

    async def originate(self, endpoint: str, args: str, caller_id: str, timeout_s: int) -> str:
        ch = await self._post("/channels", endpoint=endpoint, app=self.app, appArgs=args,
                              callerId=caller_id or None, timeout=timeout_s)
        return ch["id"]

    async def hangup(self, channel: str) -> None:
        await self.http.delete(f"/channels/{channel}")

    async def play(self, bridge: str, media: str) -> str:
        return (await self._post(f"/bridges/{bridge}/play", media=media))["id"]

    async def stop_playback(self, playback: str) -> None:
        await self.http.delete(f"/playbacks/{playback}")


class GatewayHttp:
    def __init__(self, base: str, key: str = ""):
        import httpx

        headers = {"Authorization": f"Bearer {key}"} if key else {}
        self.http = httpx.AsyncClient(base_url=base.rstrip("/"), headers=headers, timeout=10)

    async def create_call(self, call_id: str, caller: str, called: str, codec: str) -> int:
        import httpx

        try:
            r = await self.http.post("/v1/calls", json={"call_id": call_id, "caller": caller, "called": called,
                                                        "codec": codec})
        except httpx.HTTPError as e:
            raise GatewayBusy(str(e)) from e
        if r.status_code in (503, 429):
            raise GatewayBusy(r.json().get("error", {}).get("message", str(r.status_code)))
        r.raise_for_status()
        return int(r.json()["rtp_port"])

    async def event(self, call_id: str, body: dict[str, Any]) -> None:
        r = await self.http.post(f"/v1/calls/{call_id}/events", json=body)
        if r.status_code >= 300:
            log.warning("[%s] Gateway olayı reddetti (%d): %s", call_id, r.status_code, r.text[:200])

    async def end(self, call_id: str, reason: str) -> None:
        with contextlib.suppress(Exception):
            await self.http.request("DELETE", f"/v1/calls/{call_id}", json={"reason": reason})


async def run(a: argparse.Namespace) -> None:
    import uvicorn
    import websockets

    ari = AriHttp(a.ari, a.ari_kullanici, a.ari_sifre)
    bridge = Bridge(ari, GatewayHttp(a.gateway, a.gateway_anahtar), a.rtp_host, a.operator)
    app = FastAPI()

    @app.post("/v1/calls/{cid}/actions", status_code=202)
    async def action(cid: str, request: Request) -> JSONResponse:
        if a.anahtar and request.headers.get("authorization") != f"Bearer {a.anahtar}":
            return JSONResponse({"error": {"code": "UNAUTHORIZED", "message": "anahtar", "call_id": cid}}, 401)
        asyncio.create_task(bridge.on_action(cid, await request.json()))
        return JSONResponse({}, 202)

    host, port = a.dinle.rsplit(":", 1)
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=int(port), log_level="warning"))
    srv = asyncio.create_task(server.serve())
    ws_url = (a.ari.replace("http", "ws", 1).rstrip("/")
              + f"/ari/events?app={APP}&api_key={a.ari_kullanici}:{a.ari_sifre}")
    log.info("köprü hazır: eylemler http://%s, Asterisk %s", a.dinle, a.ari)
    try:
        while True:
            try:
                async with websockets.connect(ws_url) as ws:
                    log.info("Asterisk'e bağlandı (uygulama: %s)", APP)
                    async for raw in ws:
                        asyncio.create_task(bridge.on_event(json.loads(raw)))
            except (OSError, websockets.WebSocketException) as e:
                log.warning("Asterisk bağlantısı yok (%s); 3 sn sonra yeniden", e)
                await asyncio.sleep(3)
    finally:
        server.should_exit = True
        await srv


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ari", default="http://127.0.0.1:8088")
    ap.add_argument("--ari-kullanici", default="sesli")
    ap.add_argument("--ari-sifre", required=True)
    ap.add_argument("--gateway", default="http://127.0.0.1:8000")
    ap.add_argument("--gateway-anahtar", default="", help="Gateway API_KEY")
    ap.add_argument("--anahtar", default="", help="Gateway'in bu köprüye gönderdiği anahtar (PBX_API_KEY)")
    ap.add_argument("--rtp-host", default="127.0.0.1", help="Asterisk'in Gateway'e RTP göndereceği adres")
    ap.add_argument("--dinle", default="127.0.0.1:8100", help="Gateway eylemleri için (PBX_BASE_URL)")
    ap.add_argument("--operator", default="900", help="operatör dahilisi")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run(a))


if __name__ == "__main__":
    main()
