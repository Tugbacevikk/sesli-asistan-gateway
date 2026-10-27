"""Ayar ekranı (tasarim.md 13.1): anonslar, mesai kuralları ve rehbere yerel takma adlar.

Ayar dosyası (ortam değişkenleri) temel değerdir; ayar ekranında kaydedilenler onun üzerine yazılır ve
`LOCAL_SETTINGS_FILE`'da saklanır. Kaydetme anında:
  - aynı doğrulama kuralları uygulanır (hatalı yer tutucu, bozuk mesai kuralı → kaydedilmez, mesaj döner),
  - değişen anonslar yeniden seslendirilir (hazır ses kütüphanesi),
  - takma adlar rehber dizinine eklenir; rehber her güncellendiğinde yeniden uygulanır,
  - süren çağrılar yeni metinleri bir sonraki cümlede kullanır; mesai durumu yeni çağrılarda geçerlidir.
Yeniden başlatma gerekmez.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from .config import TEXT_FIELDS, check_texts
from .core.schedule import OfficeHours
from .core.search import search_key

if TYPE_CHECKING:
    from .adapters.settings.json_store import LocalSettingsStore
    from .composition import GatewayRuntime

log = logging.getLogger(__name__)

TEXT_LABELS = {
    "greeting": ("Karşılama", "{kurum} {kurum_e}"),
    "not_understood": ("Anlaşılamadı", ""),
    "offer_operator": ("Operatör önerisi", ""),
    "no_input": ("Arayan konuşmuyor", ""),
    "to_operator": ("Operatöre aktarma", ""),
    "error": ("Hata", ""),
    "transferring": ("Kişiye aktarma", "{ad} {ad_e}"),
    "transferring_department": ("Birime aktarma", "{birim}"),
    "closed": ("Mesai dışı", ""),
    "holiday": ("Tatil", ""),
    "no_operator": ("Operatör yok (mesai dışı)", ""),
}
MAX_TEXT = 400
MAX_ALIASES = 10


class SettingsService:
    def __init__(self, rt: GatewayRuntime, store: LocalSettingsStore | None):
        self.rt, self.store = rt, store
        self.local: dict[str, Any] = {}
        self._lock = asyncio.Lock()

    # --- yükleme / uygulama ---------------------------------------------------------------------------------

    def load(self) -> None:
        """Açılışta: kayıtlı yerel ayarlar uygulanır. Geçersizse (ör. ayar dosyası değişti, yer tutucu artık
        uymuyor) yok sayılır ve loglanır; Gateway ayar dosyasındaki değerlerle açılır."""
        if self.store is None:
            return
        data = self.store.load()
        if not data:
            return
        try:
            self.local = self.normalize(data, check_contacts=False)
        except ValueError as e:
            log.error("yerel ayarlar uygulanamadı (%s): ayar dosyasındaki değerlerle devam", e)
            return
        self.apply(self.local)
        log.info("yerel ayarlar uygulandı: %d anons, %d kişiye takma ad%s", len(self.local.get("texts", {})),
                 len(self.local.get("aliases", {})), ", mesai kuralı" if "office_hours" in self.local else "")

    def base_texts(self):
        return self.rt.s.dialog_texts()

    def apply(self, local: dict[str, Any]) -> None:
        rt, s = self.rt, self.rt.s
        texts = replace(self.base_texts(), **local.get("texts", {}))
        rt.agent.cfg = replace(rt.agent.cfg, texts=texts)
        rt.office = OfficeHours.parse(local.get("office_hours", s.office_hours), local.get("holidays", s.holidays))
        rt.after_hours_mode = local.get("after_hours_mode", s.after_hours_mode)
        rt.directory.index.set_extra_aliases(local.get("aliases", {}))

    # --- doğrulama ----------------------------------------------------------------------------------------

    def normalize(self, data: dict[str, Any], check_contacts: bool = True) -> dict[str, Any]:
        """Gelen ayarları temizler ve doğrular; ayar dosyasıyla aynı olanları atar (yalnız fark saklanır)."""
        if not isinstance(data, dict):
            raise ValueError("ayarlar JSON nesnesi olmalı")
        s, base = self.rt.s, self.base_texts()
        out: dict[str, Any] = {}

        texts = {}
        for name, value in (data.get("texts") or {}).items():
            if name not in TEXT_FIELDS:
                raise ValueError(f"bilinmeyen anons: {name}")
            v = " ".join(str(value or "").split())
            if len(v) > MAX_TEXT:
                raise ValueError(f"{TEXT_LABELS[name][0]}: en çok {MAX_TEXT} karakter")
            if v and v != getattr(base, name):
                texts[name] = v
        try:
            check_texts(replace(base, **texts))
        except ValueError as e:
            raise ValueError(_label_error(str(e))) from None
        if texts:
            out["texts"] = texts

        for key in ("office_hours", "holidays"):
            if key in data and str(data[key] or "").strip() != getattr(s, key).strip():
                out[key] = " ".join(str(data[key] or "").split())
        try:
            OfficeHours.parse(out.get("office_hours", s.office_hours), out.get("holidays", s.holidays))
        except ValueError as e:
            raise ValueError(f"Mesai kuralı: {e}") from None
        mode = data.get("after_hours_mode", s.after_hours_mode)
        if mode not in ("announce", "assistant"):
            raise ValueError("Mesai dışı davranış: announce ya da assistant olmalı")
        if mode != s.after_hours_mode:
            out["after_hours_mode"] = mode

        known = {c.id: c for c in self.rt.directory.index.contacts()}
        aliases: dict[str, list[str]] = {}
        for cid, values in (data.get("aliases") or {}).items():
            if check_contacts and cid not in known:
                raise ValueError(f"rehberde olmayan kayıt: {cid}")
            seen, clean = set(), []
            for v in values or []:
                v = " ".join(str(v or "").split())
                if not v:
                    continue
                if not 2 <= len(v) <= 60 or not search_key(v):
                    raise ValueError(f"takma ad 2–60 harf olmalı ve unvandan ibaret olmamalı: {v!r}")
                if search_key(v) not in seen:
                    seen.add(search_key(v))
                    clean.append(v)
            if len(clean) > MAX_ALIASES:
                raise ValueError(f"bir kayda en çok {MAX_ALIASES} takma ad")
            if clean:
                aliases[cid] = clean
        if aliases:
            out["aliases"] = aliases
        return out

    # --- ayar ekranı --------------------------------------------------------------------------------------

    @property
    def writable(self) -> bool:
        return self.store is not None

    def view(self) -> dict[str, Any]:
        rt, s, base = self.rt, self.rt.s, self.base_texts()
        texts = rt.agent.cfg.texts
        local_texts = self.local.get("texts", {})
        return {
            "writable": self.writable,
            "texts": [{"name": n, "label": TEXT_LABELS[n][0], "placeholders": TEXT_LABELS[n][1],
                       "value": getattr(texts, n), "default": getattr(base, n), "changed": n in local_texts}
                      for n in TEXT_FIELDS],
            "office": {"hours": self.local.get("office_hours", s.office_hours),
                       "holidays": self.local.get("holidays", s.holidays),
                       "mode": rt.after_hours_mode, "status": rt.office_status(), "timezone": s.timezone},
            "aliases": self.local.get("aliases", {}),
            "contacts": sorted(({"id": c.id, "name": c.full_name, "department": c.department,
                                 "extension": c.extension, "kind": c.kind, "aliases": list(c.aliases)}
                                for c in rt.directory.index.contacts()), key=lambda x: (x["kind"], x["name"])),
            "prompts_missing": rt._prompts_missing,
        }

    async def update(self, data: dict[str, Any]) -> dict[str, Any]:
        if self.store is None:
            raise ValueError("ayar ekranı salt okunur (LOCAL_SETTINGS_FILE boş)")
        async with self._lock:
            clean = self.normalize(data)
            before = self.rt.agent.cfg.texts
            await asyncio.to_thread(self.store.save, clean)
            self.local = clean
            self.apply(clean)
            missing: list[str] = []
            if self.rt.speaker is not None and self.rt.agent.cfg.texts != before:
                missing = await self.rt.speaker.library.preload(self.rt.speaker.tts, self.rt.agent.fixed_prompts())
                self.rt._prompts_missing = bool(missing)
            log.info("ayarlar kaydedildi: %d anons, %d kişiye takma ad", len(clean.get("texts", {})),
                     len(clean.get("aliases", {})))
        view = self.view()
        view["missing"] = missing
        return view


def _label_error(msg: str) -> str:
    for name, (label, _) in TEXT_LABELS.items():
        msg = msg.replace(f"{name.upper()}_TEXT", label)
    return msg
