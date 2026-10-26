"""Rehber kaynağı: JSON dosyası — Sentra Rehber API'si hazır olana kadar (tasarim.md 6.6.2).

Dosya biçimi API yanıtıyla aynıdır ({"server_time": ..., "contacts": [...]}); API gelince yalnızca
kaynak değişir (`SentraDirectory`), arama ve diyalog kodu değişmez. Dosya her okunduğunda tam liste
döner (`full=True`): dosyadan silinen kişi önbellekten de çıkar.
"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from sesli_asistan.gateway.core.ports import DirectoryPage


class JsonDirectory:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    async def fetch(self, updated_since: str | None = None) -> DirectoryPage:
        data = await asyncio.to_thread(lambda: json.loads(self.path.read_text(encoding="utf-8")))
        server_time = data.get("server_time") or datetime.now(UTC).isoformat(timespec="seconds")
        return DirectoryPage(server_time=server_time, contacts=list(data.get("contacts", [])), full=True)
