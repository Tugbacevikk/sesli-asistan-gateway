"""Rehber dosyasına yazma (tasarim.md 13.2): arayüzden yüklenen rehber JSON kaynağın dosyasına (DIRECTORY_PATH) yazılır.

Yazma atomiktir (geçici dosya + yeniden adlandırma); mevcut dosya önce `<rehber>.onceki` olarak saklanır ve tek
adımda geri dönülebilir. Okuma `json_dir.JsonDirectory`'de (rehber önbelleği dosyayı oradan yeniden okur).
"""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class JsonDirectoryFile:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.previous = self.path.with_name(self.path.name + ".onceki")

    @property
    def label(self) -> str:
        return str(self.path)

    def unwritable_reason(self) -> str | None:
        """Gateway kullanıcısı dosyaya (ya da yoksa klasörüne) yazamıyorsa neden; yazabiliyorsa None."""
        ok = os.access(self.path.parent, os.W_OK) and (not self.path.exists() or os.access(self.path, os.W_OK))
        return None if ok else (f"{self.path} Gateway tarafından yazılamıyor: DIRECTORY_PATH'i "
                                "/var/lib/sesli-asistan/rehber.json yapın (tasarim.md 13.2)")

    def has_previous(self) -> bool:
        return self.previous.exists()

    def modified(self) -> str | None:
        """Son yazılma zamanı (UTC, ISO); dosya yoksa None."""
        try:
            return datetime.fromtimestamp(self.path.stat().st_mtime, UTC).isoformat(timespec="seconds")
        except OSError:
            return None

    def write(self, doc: dict[str, Any]) -> None:
        tmp = self.path.with_name(f".{self.path.name}.yaziliyor")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        if self.path.exists():
            os.replace(self.path, self.previous)
        os.replace(tmp, self.path)

    def restore(self) -> None:
        """Önceki rehberle yer değiştir (geri al iki kez basılırsa yeniye döner)."""
        if not self.path.exists():
            os.replace(self.previous, self.path)
            return
        tmp = self.path.with_name(f".{self.path.name}.takas")
        os.replace(self.path, tmp)
        os.replace(self.previous, self.path)
        os.replace(tmp, self.previous)
