"""Ayar ekranında kaydedilen yerel ayarlar (tasarim.md 13.1): tek JSON dosyası.

Yazma atomiktir (geçici dosya + yeniden adlandırma): yazarken elektrik kesilse de dosya yarım kalmaz.
Dosya bozulursa (elle düzenleme hatası) adı `.bozuk` ile değiştirilir, Gateway ayar dosyasındaki
(ortam değişkeni) değerlerle açılır; ayarlar kaybolmaz, incelenebilir.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class LocalSettingsStore:
    def __init__(self, path: str):
        self.path = Path(path)
        self._lock = threading.Lock()

    def load(self) -> dict[str, Any]:
        with self._lock:
            if not self.path.exists():
                return {}
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("kök JSON nesnesi değil")
                return data
            except ValueError as e:
                bad = self.path.with_suffix(self.path.suffix + ".bozuk")
                os.replace(self.path, bad)
                log.error("yerel ayar dosyası okunamadı (%s): %s olarak saklandı, varsayılanlarla devam", e, bad)
                return {}

    def save(self, data: dict[str, Any]) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".ayar-", dir=self.path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self.path)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
