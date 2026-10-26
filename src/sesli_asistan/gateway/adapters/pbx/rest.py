"""Gateway → santral eylemleri: `POST {PBX_BASE_URL}/v1/calls/{call_id}/actions` (tasarim.md 6.2.2, 9.1).

  - Eylem 202 ile kabul edilir; sonuç santralden `/events` ile asenkron gelir.
  - Ağ hatası, zaman aşımı ve 5xx yanıtlarında üstel bekleme + rastgele sapma ile en çok 3 deneme. `action_id`
    sayesinde tekrar gönderim güvenlidir (santral aynı eylemi ikinci kez uygulamaz).
  - 4xx yeniden denenmez (istek hatalı; tekrar etmek düzeltmez).
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

import httpx

from sesli_asistan.gateway.core.ports import PbxError

log = logging.getLogger(__name__)


class PbxRestClient:
    def __init__(self, base_url: str, api_key: str = "", timeout_s: float = 3.0, attempts: int = 3,
                 backoff_s: float = 0.2, client: httpx.AsyncClient | None = None):
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.http = client or httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout_s, headers=headers)
        self.attempts, self.backoff_s = attempts, backoff_s

    async def send_action(self, call_id: str, action: dict[str, Any]) -> None:
        last: Exception | None = None
        for i in range(self.attempts):
            if i:
                await asyncio.sleep(self.backoff_s * 2 ** (i - 1) * (1 + random.random()))
            try:
                r = await self.http.post(f"/v1/calls/{call_id}/actions", json=action)
            except httpx.HTTPError as e:
                last = e
                log.warning("[%s] santral eylemi %s denemesi %d başarısız: %s", call_id, action["type"], i + 1, e)
                continue
            if r.status_code < 300:
                log.info("[%s] santral eylemi gönderildi: %s", call_id, action)
                return
            last = PbxError(f"HTTP {r.status_code}: {r.text[:200]}")
            if r.status_code < 500:
                break
            log.warning("[%s] santral eylemi %s denemesi %d: HTTP %d", call_id, action["type"], i + 1, r.status_code)
        raise PbxError(f"santral eylemi başarısız ({action['type']}): {last}")

    async def aclose(self) -> None:
        await self.http.aclose()
