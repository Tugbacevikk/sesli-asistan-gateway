"""Gateway servisini başlatır: `python -m sesli_asistan.gateway` (systemd: sesli-asistan-gateway.service).

REST (FastAPI/uvicorn) ve RTP aynı asyncio olay döngüsünde çalışır; ağır iş (STT, LLM, TTS) ayrı süreçlerdedir.
SIGTERM'de açık çağrılar kapatılır, sonra servis durur.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import uvicorn

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

    return create_app(rt.manager, rt.health, api_key=s.api_key.get_secret_value(), allowed_ips=s.allowed_ips,
                      lifespan=lifespan)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = GatewaySettings()
    if not s.api_key.get_secret_value():
        logging.getLogger(__name__).warning("API_KEY boş: kontrol API'si kimlik doğrulamasız (yalnızca geliştirme)")
    uvicorn.run(build_app(s), host=s.gateway_api_host, port=s.gateway_api_port, log_level="info",
                timeout_graceful_shutdown=10)


if __name__ == "__main__":
    main()
