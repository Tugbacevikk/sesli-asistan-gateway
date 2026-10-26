"""Rehber önbelleği (tasarim.md 5.5, 6.6, ADR-008).

  - Açılışta tam liste, sonra `refresh_s` aralıkla yalnızca değişenler (`updated_since`).
  - `updated_since` olarak **sunucunun** önceki yanıttaki `server_time` değeri kullanılır; Gateway'in
    kendi saati kullanılmaz (iki makinenin saat farkı yüzünden kayıt kaçmasın).
  - Silinen kayıtlar `deleted: true` ile gelir ve kopyadan çıkarılır.
  - Kaynak erişilemezse son iyi kopyayla devam edilir; kopyanın yaşı izlenir.
"""
from __future__ import annotations

import asyncio
import logging
import time

from .models import Contact
from .ports import DirectorySource
from .search import DirectoryIndex

log = logging.getLogger(__name__)


class DirectoryCache:
    def __init__(self, source: DirectorySource, index: DirectoryIndex | None = None, refresh_s: float = 300):
        self.source = source
        self.index = index or DirectoryIndex()
        self.refresh_s = refresh_s
        self._contacts: dict[str, Contact] = {}
        self._server_time: str | None = None
        self._last_ok: float | None = None   # time.monotonic()
        self._task: asyncio.Task | None = None

    @property
    def age_s(self) -> float | None:
        """Son başarılı güncellemeden bu yana geçen süre (metrik: directory_age_seconds)."""
        return None if self._last_ok is None else time.monotonic() - self._last_ok

    @property
    def ready(self) -> bool:
        return self._last_ok is not None

    async def refresh(self) -> bool:
        """Tam (ilk) ya da artımlı güncelleme. Başarısızsa False; eldeki kopya korunur."""
        try:
            page = await self.source.fetch(self._server_time)
        except Exception as e:
            log.warning("rehber güncellenemedi (%s); son kopyayla devam (yaş: %s sn)", e,
                        None if self.age_s is None else round(self.age_s))
            return False
        changed = 0
        if page.full:
            fresh = {c.id: c for c in map(Contact.from_api, page.contacts) if not c.deleted}
            changed = len(fresh.keys() ^ self._contacts.keys()) + sum(
                1 for k, c in fresh.items() if self._contacts.get(k) != c)
            self._contacts = fresh
        for raw in ([] if page.full else page.contacts):
            c = Contact.from_api(raw)
            if c.deleted:
                changed += self._contacts.pop(c.id, None) is not None
            else:
                self._contacts[c.id] = c
                changed += 1
        if changed or self._server_time is None:
            self.index.rebuild(self._contacts.values())
        self._server_time = page.server_time
        self._last_ok = time.monotonic()
        log.info("rehber güncellendi: %d değişiklik, %d kayıt (aranabilir: %d)",
                 changed, len(self._contacts), len(self.index))
        return True

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="rehber-guncelleme")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        while True:
            await self.refresh()
            await asyncio.sleep(self.refresh_s)
