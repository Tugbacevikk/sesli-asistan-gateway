"""Gateway servisini başlatır: `python -m sesli_asistan.gateway` (systemd: sesli-asistan-gateway.service).

REST (FastAPI/uvicorn) ve RTP aynı asyncio olay döngüsünde çalışır; ağır iş (STT, LLM, TTS) ayrı süreçlerdedir.
SIGTERM'de açık çağrılar kapatılır, sonra servis durur.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import uvicorn

from sesli_asistan.libs.obs import setup_logging

from .api.app import create_app
from .composition import GatewayRuntime
from .config import GatewaySettings


def build_app(s: GatewaySettings | None = None, runtime: GatewayRuntime | None = None):
    s = s or GatewaySettings()
    rt = runtime or GatewayRuntime(s)

    @asynccontextmanager
    async def lifespan(app):
        await rt.start()
        app.state.runtime = rt
        yield
        await rt.stop()

    from .api.access import UiAccess

    access = UiAccess(s.api_key.get_secret_value(), s.users(), s.ui_ips)
    return create_app(rt.manager, rt.health, api_key=s.api_key.get_secret_value(), allowed_ips=s.allowed_ips,
                      lifespan=lifespan, web_session=rt.web_session if s.ui_enabled else None,
                      ui_allowed_ips=s.ui_ips, metrics_registry=rt.metrics.registry, history=rt.history,
                      settings=rt.settings, ui_access=access)


def security_warnings(s: GatewaySettings) -> list[str]:
    """Üretimde açık bırakılmaması gereken ayarlar (açılışta loglanır; servis yine açılır)."""
    out = []
    if not s.api_key.get_secret_value():
        out.append("API_KEY boş: santral API'si kimlik doğrulamasız (yalnızca geliştirme)")
    if not s.allowed_ips:
        out.append("PBX_ALLOWED_IPS boş: santral API'si ve RTP her adresten kabul ediliyor")
    if s.ui_enabled and s.ui_ips is None and not (s.api_key.get_secret_value() or s.users()):
        out.append("UI_ALLOWED_IPS='*' ve anahtar yok: arayüz herkese açık")
    return out


def main() -> None:
    setup_logging("gateway")
    s = GatewaySettings()
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
