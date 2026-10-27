"""Santral → Gateway kontrol API'si (tasarim.md 6.2.1). OpenAPI şeması: `/v1/openapi.json`.

    POST   /v1/calls                   yeni çağrı → {call_id, rtp_port}
    POST   /v1/calls/{call_id}/events  transfer_result | dtmf
    DELETE /v1/calls/{call_id}         çağrı bitti (idempotent: bilinmeyen çağrı da 204)
    GET    /v1/health                  sağlık (zorunlu bileşen çalışmıyorsa 503)

Hata gövdesi her yerde aynı (6.2.3): {"error": {"code", "message", "call_id"}}.
Kimlik doğrulama: `Authorization: Bearer <API_KEY>`; ayrıca istek kaynağı `PBX_ALLOWED_IPS` içinde olmalı.
"""
from __future__ import annotations

import hmac
import logging
from collections.abc import Callable
from typing import Any, Literal

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from sesli_asistan.gateway.core.manager import CallError, CallManager, NewCall

from .ui import UI_PREFIXES, _ui_access, mount_ui, request_key

log = logging.getLogger(__name__)


class RtpRemote(BaseModel):
    host: str
    port: int = Field(gt=0, lt=65536)


class CreateCall(BaseModel):
    call_id: str = Field(min_length=1, max_length=128)
    caller: str = ""
    called: str = ""
    codec: Literal["pcma", "pcmu"] = "pcma"
    rtp_remote: RtpRemote | None = None


class CallCreated(BaseModel):
    call_id: str
    rtp_port: int


class CallEvent(BaseModel):
    type: Literal["transfer_result", "dtmf"]
    status: str | None = None
    digit: str | None = None


class EndCall(BaseModel):
    reason: Literal["hangup", "transferred", "error"] = "hangup"


def mask(number: str) -> str:
    """Arayan numarası loglarda maskelenir (tasarim.md 10.4): 05321234567 → 0532***4567."""
    return number if len(number) < 8 else f"{number[:4]}***{number[-4:]}"


def _error(status: int, code: str, message: str, call_id: str | None = None) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message, "call_id": call_id}}, status_code=status)


def create_app(manager: CallManager, health: Callable[[], dict[str, Any]], api_key: str = "",
               allowed_ips: set[str] | None = None, lifespan=None, web_session=None,
               ui_allowed_ips: set[str] | None = None) -> FastAPI:
    """web_session verilirse arayüz açılır (api/ui.py); arayüz yollarında santral IP listesi yerine
    `ui_allowed_ips` uygulanır."""
    app = FastAPI(title="Sesli Asistan Gateway", version="1", openapi_url="/v1/openapi.json",
                  docs_url="/v1/docs", redoc_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        if path in ("/v1/openapi.json", "/v1/docs"):
            return await call_next(request)
        host = request.client.host if request.client else ""
        if path.startswith(UI_PREFIXES):
            if web_session is None:
                return _error(404, "NOT_FOUND", "arayüz kapalı (UI_ENABLED=false)")
            if (why := _ui_access(host, request_key(request), api_key, ui_allowed_ips)) is not None:
                return _error(401, "UNAUTHORIZED", why)
            return await call_next(request)
        if allowed_ips is not None and host not in allowed_ips:
            log.warning("izin listesinde olmayan kaynaktan istek: %s %s", host, request.url.path)
            return _error(401, "FORBIDDEN_SOURCE", "kaynak adres izinli değil")
        if api_key:
            got = request.headers.get("authorization", "")
            if not hmac.compare_digest(got.encode(), f"Bearer {api_key}".encode()):
                return _error(401, "UNAUTHORIZED", "geçersiz ya da eksik anahtar")
        return await call_next(request)

    @app.exception_handler(CallError)
    async def call_error(request: Request, e: CallError):
        return _error(e.status, e.code, str(e), e.call_id or request.path_params.get("call_id"))

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, e: RequestValidationError):
        msg = "; ".join(f"{'.'.join(map(str, x['loc'][1:]))}: {x['msg']}" for x in e.errors())
        return _error(400, "BAD_REQUEST", msg, request.path_params.get("call_id"))

    @app.post("/v1/calls", status_code=201, response_model=CallCreated)
    async def create_call(body: CreateCall) -> CallCreated:
        remote = (body.rtp_remote.host, body.rtp_remote.port) if body.rtp_remote else None
        log.info("[%s] yeni çağrı: arayan %s, aranan %s, codec %s", body.call_id, mask(body.caller), body.called,
                 body.codec)
        port = await manager.create(NewCall(body.call_id, body.codec, remote, body.caller, body.called))
        return CallCreated(call_id=body.call_id, rtp_port=port)

    @app.post("/v1/calls/{call_id}/events", status_code=202)
    async def call_event(call_id: str, body: CallEvent) -> Response:
        manager.event(call_id, body.type, body.model_dump(exclude_none=True))
        return Response(status_code=202)

    @app.delete("/v1/calls/{call_id}", status_code=204)
    async def end_call(call_id: str, request: Request) -> Response:
        reason = "hangup"
        if raw := await request.body():
            try:
                reason = EndCall.model_validate_json(raw).reason
            except ValidationError as e:
                return _error(400, "BAD_REQUEST", str(e.errors()[0]["msg"]), call_id)
        await manager.end(call_id, reason)
        return Response(status_code=204)

    if web_session is not None:
        mount_ui(app, manager, web_session, health, api_key, ui_allowed_ips)

    @app.get("/v1/health")
    async def get_health() -> JSONResponse:
        h = health()
        return JSONResponse(h, status_code=200 if h.get("status") == "ok" else 503)

    return app
