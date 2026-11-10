"""Rehber yükleme (tasarim.md 13.2): önizle → onayla → rehber dosyasına yaz (önceki sürüm saklanır) → yeniden oku.
Dosyayı okuma ve karşılaştırma `directory_import`'ta; burada akış, belirteç, atomik yazma, geri alma, denetim kaydı."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import directory_import as dimp
from .core.models import Contact
from .local_settings import LocalSettings

if TYPE_CHECKING:
    from .composition import GatewayRuntime

log = logging.getLogger(__name__)


class DirectoryUpload:
    """Rehber yükleme (tasarim.md 13.2): önizle → onayla → rehber dosyasına yaz → rehberi yeniden oku.

    Önizleme sonucu 15 dk bellekte tutulur ve bir belirteçle onaylanır (dosya ikinci kez gönderilmez; önizlenen ile
    uygulanan aynıdır). Yazmadan önce mevcut dosya `<rehber>.onceki` olarak saklanır: `revert()` tek adımda geri
    döner. Yalnız JSON kaynakta (DIRECTORY_SOURCE=json) ve dosya Gateway kullanıcısınca yazılabilirse açıktır."""

    TTL_S = 900

    def __init__(self, rt: GatewayRuntime):
        self.rt = rt
        self.path = Path(rt.s.directory_path) if rt.s.directory_source == "json" else None
        self._pending: dict[str, tuple[float, list[dict[str, Any]], dict[str, Any]]] = {}
        self._lock = asyncio.Lock()

    @property
    def previous(self) -> Path | None:
        return None if self.path is None else self.path.with_name(self.path.name + ".onceki")

    def status(self) -> dict[str, Any]:
        """Arayüz için: yükleme açık mı, değilse neden; geri alınabilir mi."""
        if self.path is None:
            return {"enabled": False, "reason": "rehber dış kaynaktan (API) geliyor; yükleme kapalı"}
        folder = self.path.parent
        ok = os.access(folder, os.W_OK) and (not self.path.exists() or os.access(self.path, os.W_OK))
        prev = self.previous
        out: dict[str, Any] = {"enabled": ok, "file": str(self.path),
                               "revertible": bool(ok and prev is not None and prev.exists())}
        if not ok:
            out["reason"] = (f"{self.path} Gateway tarafından yazılamıyor: DIRECTORY_PATH'i "
                             "/var/lib/sesli-asistan/rehber.json yapın (tasarim.md 13.2)")
        try:
            out["modified"] = datetime.fromtimestamp(self.path.stat().st_mtime, UTC).isoformat(timespec="seconds")
        except OSError:
            pass
        return out

    def _source(self) -> list[Contact]:
        return self.rt.directory.index.source_contacts()

    async def preview(self, data: bytes, filename: str = "", mode: str = "replace") -> dict[str, Any]:
        """`mode`: "replace" (dosya rehberin tamamı; dosyada olmayan silinir) ya da "merge" (mevcut rehbere eklenir,
        hiçbir kayıt silinmez; aynı kayıt dosyadakiyle güncellenir)."""
        if mode not in ("replace", "merge"):
            raise ValueError("yükleme biçimi replace ya da merge olmalı")
        st = self.status()
        if not st["enabled"]:
            raise ValueError(st["reason"])
        rows = await asyncio.to_thread(dimp.read_table, data, filename)
        old = self._source()
        res = await asyncio.to_thread(dimp.parse, rows, old, getattr(self.rt, "abbreviations", None))
        if not res.contacts:
            raise ValueError("dosyada alınabilecek kayıt yok"
                             + (f": {res.errors[0].message} ({res.errors[0].row}. satır)" if res.errors else ""))
        dimp.keep_unchanged_timestamps(old, res.contacts)
        contacts = dimp.merge(old, res.contacts) if mode == "merge" else res.contacts
        d = dimp.diff(old, contacts)
        warnings = [p.as_dict() for p in res.warnings]
        # yerel katman: takma adı olan kayıt yeni rehberde yoksa takma ad etkisiz kalır; yerel kayıtla dahili çakışması
        local = self.rt.settings.local if getattr(self.rt, "settings", None) else LocalSettings()
        new_ids = {c["id"] for c in contacts}
        orphan = [cid for cid in local.aliases if cid not in new_ids and not cid.startswith("yerel-")]
        if orphan:
            names = {c.id: c.full_name for c in old}
            warnings.append({"row": 0, "message": "takma adları olan kayıtlar yeni rehberde yok, takma adlar "
                             "etkisiz kalacak: " + ", ".join(names.get(c, c) for c in orphan[:5])})
        new_ext = {c["extension"] for c in contacts}
        clash = [c for c in local.contacts if c.extension in new_ext]
        if clash:
            warnings.append({"row": 0, "message": "arayüzden eklenen yerel kayıtların dahilisi artık rehberde de "
                             "var (yerel kaydı silebilirsiniz): " + ", ".join(c.extension for c in clash[:5])})
        now = time.monotonic()
        self._pending = {k: v for k, v in self._pending.items() if v[0] > now}
        token = secrets.token_urlsafe(16)
        summary = {"rows": res.rows, "contacts": len(contacts), "errors": len(res.errors), "skipped": res.skipped,
                   "mode": mode, "from_file": len(res.contacts),
                   "diff": {k: d[k] for k in ("added", "removed", "changed", "unchanged")}}
        self._pending[token] = (now + self.TTL_S, contacts, summary)
        if len(self._pending) > 5:            # bellekte sınırlı önizleme
            self._pending.pop(next(iter(self._pending)))
        return {"token": token, **summary, "columns": res.columns, "diff": d,
                "error_list": [p.as_dict() for p in res.errors[:200]], "warnings": warnings[:200],
                "sample": res.contacts[:15]}

    async def apply(self, token: str, skip_errors: bool, actor: dict[str, str]) -> dict[str, Any]:
        item = self._pending.get(token)
        if item is None or item[0] < time.monotonic():
            raise ValueError("önizleme süresi doldu ya da bulunamadı: dosyayı yeniden yükleyin")
        _, contacts, summary = item
        if summary["errors"] and not skip_errors:
            raise ValueError(f"{summary['errors']} hatalı satır var: düzeltip yeniden yükleyin ya da "
                             "'hatalı satırları atla' ile onaylayın")
        async with self._lock:
            assert self.path is not None
            doc = {"server_time": datetime.now(UTC).isoformat(timespec="seconds"), "contacts": contacts,
                   "_kaynak": "arayüzden yüklendi"}
            await asyncio.to_thread(self._write, doc)
            self._pending.pop(token, None)
            if not await self.rt.directory.refresh():
                await asyncio.to_thread(self._restore)
                await self.rt.directory.refresh()
                raise ValueError("yeni rehber okunamadı; önceki rehbere dönüldü")
        d = summary["diff"]
        verb = "rehbere eklendi" if summary.get("mode") == "merge" else "rehber yüklendi"
        msg = (f"{verb}: {summary['contacts']} kayıt (+{d['added']} −{d['removed']} ~{d['changed']}"
               + (f", {summary['errors']} hatalı satır atlandı)" if summary["errors"] else ")"))
        await self._audit(actor, msg)
        return {"ok": True, "message": msg, "status": self.status()}

    async def revert(self, actor: dict[str, str]) -> dict[str, Any]:
        st = self.status()
        if not st.get("revertible"):
            raise ValueError("geri alınacak önceki rehber yok")
        async with self._lock:
            await asyncio.to_thread(self._restore)
            await self.rt.directory.refresh()
        msg = f"rehber önceki sürüme döndü: {len(self._source())} kayıt"
        await self._audit(actor, msg)
        return {"ok": True, "message": msg, "status": self.status()}

    def export(self) -> bytes:
        return dimp.export_csv(self._source())

    def _write(self, doc: dict[str, Any]) -> None:
        assert self.path is not None and self.previous is not None
        tmp = self.path.with_name(f".{self.path.name}.yaziliyor")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        if self.path.exists():
            os.replace(self.path, self.previous)
        os.replace(tmp, self.path)

    def _restore(self) -> None:
        """Önceki rehberle yer değiştir (geri al iki kez basılırsa yeniye döner)."""
        assert self.path is not None and self.previous is not None
        if not self.path.exists():
            os.replace(self.previous, self.path)
            return
        tmp = self.path.with_name(f".{self.path.name}.takas")
        os.replace(self.path, tmp)
        os.replace(self.previous, self.path)
        os.replace(tmp, self.previous)

    async def _audit(self, actor: dict[str, str], msg: str) -> None:
        log.info("%s (%s, %s)", msg, actor.get("kim"), actor.get("ip"))
        try:
            await asyncio.to_thread(self.rt.history.change_add, actor.get("kim", "?"), actor.get("rol", "?"),
                                    actor.get("ip", "?"), "rehber", [msg])
        except Exception:
            log.exception("değişiklik geçmişi yazılamadı")
