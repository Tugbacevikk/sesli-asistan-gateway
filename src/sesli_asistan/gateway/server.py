"""Gateway servisini başlatır: `python -m sesli_asistan.gateway` (systemd: sesli-asistan-gateway.service).

REST (FastAPI/uvicorn) ve RTP aynı asyncio olay döngüsünde çalışır; ağır iş (STT, LLM, TTS) ayrı süreçlerdedir.
SIGTERM'de açık çağrılar kapatılır, sonra servis durur.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from sesli_asistan.libs.obs import setup_logging

from .api.app import create_app
from .composition import GatewayRuntime
from .config import GatewaySettings


def build_app(s: GatewaySettings | None = None, runtime: GatewayRuntime | None = None) -> FastAPI:
    s = s or GatewaySettings()
    rt = runtime or GatewayRuntime(s)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await rt.start()
        app.state.runtime = rt
        yield
        await rt.stop()

    from .api.access import UiAccess

    access = UiAccess(s.api_key.get_secret_value(), s.users(), s.ui_ips)
    return create_app(rt.manager, rt.health, api_key=s.api_key.get_secret_value(), allowed_ips=s.allowed_ips,
                      lifespan=lifespan, web_session=rt.web_session if s.ui_enabled else None,
                      ui_allowed_ips=s.ui_ips, metrics_registry=rt.metrics.registry, history=rt.history,
                      settings=rt.settings, ui_access=access, live=rt.live)


def _loopback(host: str) -> bool:
    import ipaddress

    if host.strip().lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip()).is_loopback
    except ValueError:
        return False


def security_errors(s: GatewaySettings) -> list[str]:
    """Servisi açtırmayan ayarlar: ağa açık adreste kimlik doğrulamasız santral API'si (çağrı açma, olay, kapatma
    herkese açık olurdu). Kurulum (install.sh) API_KEY'i üretir; elle kurulumda unutulursa servis açılmaz."""
    if s.api_key.get_secret_value() or _loopback(s.gateway_api_host) or s.allow_insecure_api:
        return []
    return [f"API_KEY boş ve GATEWAY_API_HOST={s.gateway_api_host} (ağa açık): santral API'si kimlik doğrulamasız "
            "olurdu. secrets.env'e API_KEY yazın (santral aynı anahtarı Bearer olarak gönderir); yalnız deneme için "
            "GATEWAY_API_HOST=127.0.0.1 ya da ALLOW_INSECURE_API=true."]


def security_warnings(s: GatewaySettings) -> list[str]:
    """Üretimde açık bırakılmaması gereken ayarlar (açılışta loglanır; servis yine açılır)."""
    out = []
    if not s.api_key.get_secret_value():
        out.append("API_KEY boş: santral API'si kimlik doğrulamasız (yalnızca geliştirme"
                   + (", ALLOW_INSECURE_API=true)" if s.allow_insecure_api else ", yalnız bu sunucudan)"))
    if not s.allowed_ips:
        out.append("PBX_ALLOWED_IPS boş: santral API'si ve RTP her adresten kabul ediliyor")
    if s.ui_enabled and s.ui_ips is None and not (s.api_key.get_secret_value() or s.users()):
        out.append("UI_ALLOWED_IPS='*' ve anahtar yok: arayüz herkese açık")
    return out


def main() -> None:
    setup_logging("gateway")
    s = GatewaySettings()
    from sesli_asistan import __version__

    logging.getLogger(__name__).info("sesli asistan Gateway %s", __version__)
    if errors := security_errors(s):
        for e in errors:
            logging.getLogger(__name__).error("GÜVENLİK: %s", e)
        raise SystemExit(2)
    for w in security_warnings(s):
        logging.getLogger(__name__).warning("GÜVENLİK: %s", w)
    if s.ui_enabled:
        logging.getLogger(__name__).info("arayüz: http://%s:%d/ui", "127.0.0.1" if s.gateway_api_host == "0.0.0.0"
                                         else s.gateway_api_host, s.gateway_api_port)
    uvicorn.run(build_app(s), host=s.gateway_api_host, port=s.gateway_api_port, log_level="info", log_config=None,
                timeout_graceful_shutdown=10, proxy_headers=bool(s.forwarded_allow_ips.strip()),
                forwarded_allow_ips=s.forwarded_allow_ips.strip() or None)


if __name__ == "__main__":
    main()
