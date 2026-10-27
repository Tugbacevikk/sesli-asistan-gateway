"""Arayüz (ADR-014): tarayıcıdan deneme çağrısı + izleme.

    GET  /ui                   tek sayfa (Deneme · İzleme)
    WS   /v1/ui/ws             tarayıcı ↔ Gateway: mikrofon sesi → çağrı oturumu → asistanın sesi + olaylar
    GET  /v1/admin/calls       süren ve son çağrılar, bulunamayan aramalar
    GET  /v1/admin/health      sağlık (santral IP listesinden bağımsız)

Deneme çağrısı, santral çağrısıyla **aynı** çağrı oturumunu (`CallSession`) kullanır; yalnızca ses yolu (RTP yerine
WebSocket) ve santral (aktarma sonucunu arayüzdeki kişi seçer) farklıdır. Böylece arayüzde görülen davranış
telefondakiyle aynıdır.

WebSocket protokolü:
  tarayıcı → Gateway   ikili: 8 kHz mono PCM16 (little-endian), herhangi uzunlukta
                       metin: {"type": "text", "text": ...} (yazarak konuşma) | {"type": "transfer_result", ...}
                              | {"type": "dtmf", "digit": ...} | {"type": "hangup"}
  `?mod=metin`: yazarak deneme (mikrofon yok); ses yine çalınır, sessizlik uyarısı ve ses zaman aşımı kapalı.
  `?mesai=acik|mesai_disi|tatil`: mesai durumunu zorla (kuralları saati beklemeden denemek için).
  Gateway → tarayıcı   ikili: çalınacak 8 kHz mono PCM16, gerçek zaman hızında (20 ms'lik parçalar)
                       metin: {"type": "call", "call_id"} | {"type": "state" | "heard" | "reply" | "turn" | "action"
                              | "barge_in" | "ended", ...} | {"type": "clear"} (çalmayı hemen kes) | {"type": "error"}
Ses telefon kalitesine (8 kHz, G.711 A-law) indirilerek STT'ye verilir: deneme gerçek hattaki kaliteyle yapılır.

Erişim: `UI_ENABLED`; istemci adresi `UI_ALLOWED_IPS` içinde olmalı (varsayılan yalnızca bu makine); `API_KEY`
tanımlıysa `Authorization: Bearer` ya da `?anahtar=` gerekir (tarayıcı WebSocket'e başlık ekleyemez).
"""
from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import logging
import secrets
import time
from collections.abc import Callable
from importlib import resources
from typing import Any

import numpy as np
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

from sesli_asistan.gateway.core.call import CallSession
from sesli_asistan.gateway.core.manager import CallError, CallManager
from sesli_asistan.libs.audio import g711

from .access import VIEWER, Principal, UiAccess

log = logging.getLogger(__name__)

FRAME = 160                          # 20 ms @ 8 kHz
PLAY_LEAD_S = 0.12                   # tarayıcıya bu kadar önden gönder (ağ titremesinde boşluk olmasın)
UI_PREFIXES = ("/ui", "/v1/ui", "/v1/admin")

WebSessionFactory = Callable[..., CallSession]   # (call_id, media, pbx, observer, text_mode=False)


class _Outbox:
    """Tarayıcıya giden her şey tek sırayla gönderilir (ses ve olaylar karışmasın, eşzamanlı gönderim olmasın)."""

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.q: asyncio.Queue[bytes | dict | None] = asyncio.Queue()
        self.task = asyncio.create_task(self._run(), name="arayuz-gonderici")

    def put(self, item: bytes | dict) -> None:
        self.q.put_nowait(item)

    async def _run(self) -> None:
        with contextlib.suppress(Exception):
            while (item := await self.q.get()) is not None:
                if isinstance(item, bytes):
                    await self.ws.send_bytes(item)
                else:
                    await self.ws.send_text(json.dumps(item, ensure_ascii=False))

    async def close(self) -> None:
        self.q.put_nowait(None)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self.task, 2)


class WsMedia:
    """Tarayıcının ses yolu → çekirdeğin `MediaPort`'u (RTP'nin yerine)."""

    def __init__(self, outbox: _Outbox, codec: str = "pcma", max_queue: int = 500, text_mode: bool = False):
        self.out, self.codec, self.text_mode = outbox, codec, text_mode
        self.q: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=max_queue)
        self._buf = bytearray()
        self.opened_at = self.last = time.monotonic()
        self.received = self.sent = self.dropped = 0

    def feed(self, data: bytes) -> None:
        """Tarayıcıdan gelen PCM16 → 20 ms'lik G.711 kareleri."""
        self._buf += data
        self.last = time.monotonic()
        while len(self._buf) >= FRAME * 2:
            pcm = np.frombuffer(bytes(self._buf[:FRAME * 2]), "<i2")
            del self._buf[:FRAME * 2]
            self.received += 1
            frame = g711.encode_int16(pcm, self.codec)
            if self.q.full():
                self.q.get_nowait()
                self.dropped += 1
            self.q.put_nowait(frame)

    async def frames(self):
        while (f := await self.q.get()) is not None:
            yield f

    async def play(self, pcm8k: np.ndarray) -> int:
        loop = asyncio.get_running_loop()
        t0, n = loop.time(), 0
        try:
            for i in range(0, len(pcm8k), FRAME):
                delay = t0 + n * FRAME / 8000 - PLAY_LEAD_S - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                self.out.put(pcm8k[i:i + FRAME].astype("<i2").tobytes())
                n += 1
            return n
        except asyncio.CancelledError:
            self.out.put({"type": "clear"})   # söze girildi: tarayıcı önden aldığı sesi de atsın
            raise
        finally:
            self.sent += n

    def idle_s(self) -> float:
        return 0.0 if self.text_mode else time.monotonic() - self.last   # yazarak denemede ses gelmez

    def stats(self) -> dict[str, int]:
        return {"paket": self.received, "gonderilen": self.sent, "kuyruk_tasmasi": self.dropped}

    def close(self) -> None:
        with contextlib.suppress(asyncio.QueueFull):
            self.q.put_nowait(None)


class WebPbx:
    """Santralin yerine: eylemi arayüze gösterir; aktarma sonucunu arayüzdeki kişi seçer."""

    def __init__(self, outbox: _Outbox):
        self.out = outbox

    async def send_action(self, call_id: str, action: dict[str, Any]) -> None:
        self.out.put({"type": "pbx_action", "action": action["type"], "extension": action.get("extension"),
                      "action_id": action["action_id"]})


def _err(status: int, code: str, msg: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": msg, "call_id": None}}, status_code=status)


def request_key(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    return auth[7:] if auth.startswith("Bearer ") else request.query_params.get("anahtar", "")


def _actor(request: Request) -> dict[str, str]:
    p: Principal = getattr(request.state, "principal", None) or Principal("?", VIEWER)
    return {"kim": p.name, "rol": p.role, "ip": request.client.host if request.client else "?"}


def _forbidden(request: Request) -> JSONResponse | None:
    p = getattr(request.state, "principal", None)
    if p is None or not p.is_admin:
        return _err(403, "FORBIDDEN", "bu işlem yönetici rolü ister (izleyici yalnızca görür)")
    return None


def mount_ui(app: FastAPI, manager: CallManager, web_session: WebSessionFactory,
             health: Callable[[], dict[str, Any]], api_key: str = "", allowed_ips: set[str] | None = None,
             history: Any = None, settings: Any = None, access: UiAccess | None = None) -> None:
    access = access or UiAccess(api_key, None, allowed_ips)
    if history is None:
        from sesli_asistan.gateway.adapters.history.sqlite import MemoryCallHistory

        history = MemoryCallHistory(manager)

    @app.get("/ui", response_class=HTMLResponse, include_in_schema=False)
    async def page() -> HTMLResponse:
        html = resources.files("sesli_asistan.gateway.api").joinpath("ui.html").read_text(encoding="utf-8")
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    @app.get("/v1/admin/health", include_in_schema=False)
    async def admin_health() -> JSONResponse:
        return JSONResponse(health())

    @app.get("/v1/admin/settings", include_in_schema=False)
    async def admin_settings(request: Request) -> JSONResponse:
        if settings is None:
            return _err(404, "NOT_FOUND", "ayar ekranı yok")
        return JSONResponse(settings.view(request.state.principal))

    @app.put("/v1/admin/settings", include_in_schema=False)
    async def admin_settings_save(request: Request) -> JSONResponse:
        def err(status: int, code: str, msg: str) -> JSONResponse:
            return JSONResponse({"error": {"code": code, "message": msg, "call_id": None}}, status_code=status)

        if settings is None:
            return err(404, "NOT_FOUND", "ayar ekranı yok")
        if (denied := _forbidden(request)) is not None:
            return denied
        # yalnız JSON: başka bir sitenin form gönderimi (CSRF) tarayıcı ön kontrolüne takılır
        if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
            return err(415, "UNSUPPORTED_MEDIA_TYPE", "içerik application/json olmalı")
        try:
            data = json.loads(await request.body() or b"{}")
            view = await settings.update(data, actor=_actor(request))
        except ValueError as e:
            return err(400, "BAD_REQUEST", str(e))
        log.info("ayarlar arayüzden kaydedildi (%s)", request.client.host if request.client else "?")
        return JSONResponse(view)

    @app.post("/v1/admin/contacts", include_in_schema=False)
    async def admin_contact_add(request: Request) -> JSONResponse:
        """Rehbere yerel kayıt (kaynak rehber değişmez; tasarim.md 13.1)."""
        if settings is None:
            return _err(404, "NOT_FOUND", "ayar ekranı yok")
        if (denied := _forbidden(request)) is not None:
            return denied
        if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
            return _err(415, "UNSUPPORTED_MEDIA_TYPE", "içerik application/json olmalı")
        try:
            view = await settings.add_contact(json.loads(await request.body() or b"{}"), actor=_actor(request))
        except ValueError as e:
            return _err(400, "BAD_REQUEST", str(e))
        return JSONResponse(view, status_code=201)

    @app.delete("/v1/admin/contacts/{contact_id}", include_in_schema=False)
    async def admin_contact_remove(contact_id: str, request: Request) -> JSONResponse:
        if settings is None:
            return _err(404, "NOT_FOUND", "ayar ekranı yok")
        if (denied := _forbidden(request)) is not None:
            return denied
        try:
            view = await settings.remove_contact(contact_id, actor=_actor(request))
        except ValueError as e:
            return _err(400, "BAD_REQUEST", str(e))
        return JSONResponse(view)

    @app.get("/v1/admin/changes", include_in_schema=False)
    async def admin_changes() -> JSONResponse:
        """Ayar ve rehber değişiklik geçmişi (kim, ne zaman, nereden, ne)."""
        get = getattr(history, "changes", None)
        return JSONResponse({"changes": await asyncio.to_thread(get, 50) if get else []})

    @app.get("/v1/admin/calls", include_in_schema=False)
    async def admin_calls() -> JSONResponse:
        active = [{"call_id": cid, "channel": s.stats.channel, "state": s.state.value, "started": s.stats.started,
                   "turns": len(s.stats.turns)} for cid, s in manager.sessions.items()]
        recent, misses, today = await asyncio.gather(asyncio.to_thread(history.recent, 50),
                                                     asyncio.to_thread(history.misses, 20),
                                                     asyncio.to_thread(history.summary, 1))
        return JSONResponse({"active": active, "recent": recent, "misses": misses, "today": today})

    @app.websocket("/v1/ui/ws")
    async def ws(websocket: WebSocket) -> None:
        host = websocket.client.host if websocket.client else ""
        key = websocket.query_params.get("anahtar", "")
        principal, why = access.check(host, key)
        if principal is None:
            log.warning("arayüz bağlantısı reddedildi (%s): %s", host, why)
            await websocket.close(code=1008, reason=why)
            return
        await websocket.accept()
        out = _Outbox(websocket)
        call_id = f"web-{secrets.token_hex(3)}"
        text_mode = websocket.query_params.get("mod") == "metin"
        office = websocket.query_params.get("mesai") or None     # deneme: acik | mesai_disi | tatil
        media, pbx = WsMedia(out, text_mode=text_mode), WebPbx(out)
        session = web_session(call_id, media, pbx, lambda kind, data: out.put({**data, "type": kind}),
                              text_mode=text_mode, office=office)
        try:
            task = manager.attach(session)
        except CallError as e:
            out.put({"type": "error", "code": e.code, "message": str(e)})
            await out.close()
            await websocket.close()
            return
        out.put({"type": "call", "call_id": call_id})

        async def receive() -> None:
            while True:
                msg = await websocket.receive()
                if msg["type"] == "websocket.disconnect":
                    return
                if msg.get("bytes"):
                    media.feed(msg["bytes"])
                elif msg.get("text"):
                    try:
                        m = json.loads(msg["text"])
                    except ValueError:
                        continue
                    if m.get("type") == "hangup":
                        return
                    if m.get("type") == "text":
                        if str(m.get("text", "")).strip():
                            session.caller_text(str(m["text"])[:500])
                        continue
                    with contextlib.suppress(CallError):
                        manager.event(call_id, m.get("type", ""), m)

        reader = asyncio.create_task(receive(), name=f"arayuz-okuyucu-{call_id}")
        try:
            await asyncio.wait({reader, task}, return_when=asyncio.FIRST_COMPLETED)
        except WebSocketDisconnect:
            pass
        finally:
            await manager.end(call_id, "hangup")
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await reader
            stats = next((st for cid, st in reversed(manager.results) if cid == call_id), None)
            if stats is not None:
                out.put({"type": "summary", **dataclasses.asdict(stats)})
            await out.close()
            with contextlib.suppress(Exception):
                await websocket.close()
