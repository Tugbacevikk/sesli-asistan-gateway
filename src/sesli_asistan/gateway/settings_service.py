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
import re
import secrets
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from .config import TEXT_FIELDS, check_texts
from .core.dialog import DialogTexts
from .core.models import Contact
from .core.schedule import OfficeHours
from .core.search import search_key
from .directory_upload import DirectoryUpload
from .local_settings import AfterHoursMode, LocalContact, LocalSettings

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
    "not_found": ("Bulunamadı", ""),
    "not_found_again": ("Yine bulunamadı (operatör teklifi)", ""),
    "too_many": ("Çok aday", ""),
    "busy": ("Aranan meşgul", "{ad} {ad_e}"),
    "no_answer": ("Aranan cevap vermiyor", "{ad} {ad_e}"),
    "transfer_failed": ("Aktarma başarısız", "{ad} {ad_e}"),
    "goodbye": ("Arayan kapatmak istedi (veda)", ""),
    "confirm": ("Teyit sorusu (düşük STT güveni)", "{ad} {ad_e} {mi}"),
    "confirm_operator": ("Operatör teyidi (hedef anlaşılmadı)", ""),
    "declined": ("Teyide \"hayır\" (öneri reddedildi)", ""),
    "office_hours": ("Mesai sorusunun cevabı", "{saatler}"),
    "office_hours_unknown": ("Mesai sorusu (saatler tanımlı değil)", ""),
    "ask_target": ("Bilgiden sonra yönlendirme sorusu", ""),
    "which_department": ("Aynı unvanlı çok hat: hangi birim sorusu", "{unvan}"),
    "indistinct": ("Aynı unvan, aynı birim: operatöre aktarma", ""),
    "ask_person_in_department": ("Birim belli, kişi değil: kiminle sorusu", "{birimden}"),
    "fax_line": ("Belgegeçer / faks hattı istendi: teyitten önce söylenen cümle", ""),
    "ask_topic": ("Hedefsiz genel istek (\"bilgi almak istiyorum\")", ""),
    "no_input_question": ("Soru açıkken ses gelmedi (sorudan önce)", ""),
    "greeting_reply": ("Arayan yalnız selam verdi", ""),
}
MAX_TEXT = 400
MAX_ALIASES = 10
MAX_LOCAL_CONTACTS = 500
_EXTENSION = re.compile(r"^\d{2,6}$")   # yalnız dahili numara: dış hat / cep numarası yazılamaz (aktarma güvenliği)


class SettingsService:
    def __init__(self, rt: GatewayRuntime, store: LocalSettingsStore | None, upload: DirectoryUpload):
        self.rt, self.store, self.upload = rt, store, upload
        self.local = LocalSettings()
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
        log.info("yerel ayarlar uygulandı: %d anons, %d kişiye takma ad%s", len(self.local.texts),
                 len(self.local.aliases), ", mesai kuralı" if self.local.office_hours is not None else "")

    def base_texts(self) -> DialogTexts:
        return self.rt.s.dialog_texts()

    def apply(self, local: LocalSettings) -> None:
        rt = self.rt
        rt.agent.set_texts(replace(self.base_texts(), **local.texts))
        rt.set_office_rules(OfficeHours.parse(*self._office(local)), self._mode(local))
        rt.directory.index.set_overlay(local.aliases, [Contact.from_api(c.as_api()) for c in local.contacts])

    def _office(self, local: LocalSettings) -> tuple[str, str]:
        """Geçerli (mesai saatleri, tatiller): yerel ayar yoksa ayar dosyasındaki."""
        s = self.rt.s
        return (s.office_hours if local.office_hours is None else local.office_hours,
                s.holidays if local.holidays is None else local.holidays)

    def _mode(self, local: LocalSettings) -> AfterHoursMode:
        return self.rt.s.after_hours_mode if local.after_hours_mode is None else local.after_hours_mode

    # --- doğrulama ----------------------------------------------------------------------------------------

    def normalize(self, data: dict[str, Any], check_contacts: bool = True) -> LocalSettings:
        """Gelen ayarları (JSON) temizler ve doğrular; ayar dosyasıyla aynı olanları atar (yalnız fark saklanır)."""
        if not isinstance(data, dict):
            raise ValueError("ayarlar JSON nesnesi olmalı")
        contacts = self._clean_contacts(data.get("contacts", [c.as_api() for c in self.local.contacts]))
        known = {c.id for c in self.rt.directory.index.source_contacts() if c.transferable} | {c.id for c in contacts}
        aliases = {}
        for cid, values in (data.get("aliases") or {}).items():
            if check_contacts and cid not in known:
                raise ValueError(f"rehberde olmayan kayıt: {cid}")
            if clean := _clean_aliases(values):
                aliases[cid] = clean
        return LocalSettings(texts=self._clean_texts(data.get("texts") or {}), **self._clean_office(data),
                             aliases=aliases, contacts=tuple(contacts))

    def _clean_texts(self, data: dict[str, Any]) -> dict[str, str]:
        """Anonslar: bilinen alan, uzunluk, yer tutucular ({ad}, {saatler} …); varsayılanla aynı olan atılır."""
        base = self.base_texts()
        texts = {}
        for name, value in data.items():
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
        return texts

    def _clean_office(self, data: dict[str, Any]) -> dict[str, Any]:
        """Mesai saatleri, tatiller ve mesai dışı davranışı (ayar dosyasından farklıysa)."""
        s = self.rt.s
        out: dict[str, Any] = {}
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
        return out

    def _clean_contacts(self, items: Any) -> list[LocalContact]:
        """Yerel kayıtlar: biçim, zorunlu alanlar, dahili numara çakışması (kaynak rehberle ve kendi aralarında)."""
        if not isinstance(items, list):
            raise ValueError("kayıtlar liste olmalı")
        if len(items) > MAX_LOCAL_CONTACTS:
            raise ValueError(f"en çok {MAX_LOCAL_CONTACTS} yerel kayıt")
        taken = {c.extension: c.full_name for c in self.rt.directory.index.source_contacts()}
        names = {search_key(c.full_name) for c in self.rt.directory.index.source_contacts() if c.kind == "person"}
        return [_clean_contact(raw, taken, names) for raw in items]

    async def add_contact(self, contact: dict[str, Any], actor: dict[str, str] | None = None) -> dict[str, Any]:
        if not isinstance(contact, dict):
            raise ValueError("kayıt JSON nesnesi olmalı")
        contacts = [*(c.as_api() for c in self.local.contacts), {**contact, "id": ""}]
        view = await self.update({**self.local.to_json(), "contacts": contacts}, actor=actor, action="rehber")
        log.info("yerel kayıt eklendi: %s", self.local.contacts[-1].extension)
        return view

    async def remove_contact(self, contact_id: str, actor: dict[str, str] | None = None) -> dict[str, Any]:
        rest = [c.as_api() for c in self.local.contacts if c.id != contact_id]
        if len(rest) == len(self.local.contacts):
            raise ValueError("yalnız arayüzden eklenen (yerel) kayıtlar silinebilir")
        aliases = {k: v for k, v in self.local.aliases.items() if k != contact_id}
        return await self.update({**self.local.to_json(), "contacts": rest, "aliases": aliases}, actor=actor,
                                 action="rehber")

    # --- ayar ekranı --------------------------------------------------------------------------------------

    @property
    def writable(self) -> bool:
        return self.store is not None

    def view(self, principal: Any = None) -> dict[str, Any]:
        rt, s, base, local = self.rt, self.rt.s, self.base_texts(), self.local
        # yerel kayıt sonradan kaynak rehbere de girdiyse (aynı dahili): yerel olan silinebilir diye uyarılır
        source_ext = {c.extension: c.full_name for c in rt.directory.index.source_contacts()}
        texts = rt.agent.texts
        role = getattr(principal, "role", "yonetici")
        return {
            "writable": self.writable and role == "yonetici",
            "user": {"name": getattr(principal, "name", ""), "role": role},
            "texts": [{"name": n, "label": TEXT_LABELS[n][0], "placeholders": TEXT_LABELS[n][1],
                       "value": getattr(texts, n), "default": getattr(base, n), "changed": n in local.texts}
                      for n in TEXT_FIELDS],
            "office": dict(zip(("hours", "holidays"), self._office(local), strict=True),
                           mode=rt.after_hours_mode, status=rt.office_status(), timezone=s.timezone),
            "aliases": local.aliases,
            "contacts": sorted(({"id": c.id, "name": c.full_name, "department": c.department, "title": c.title,
                                 "extension": c.extension, "kind": c.kind, "aliases": list(c.aliases),
                                 "local": c.id.startswith("yerel-"),
                                 "duplicate": source_ext.get(c.extension) if c.id.startswith("yerel-") else None}
                                for c in rt.directory.index.contacts()),
                               key=lambda x: (x["department"].casefold(), x["kind"] != "department",
                                              x["name"].casefold())),
            "prompts_missing": rt.prompts_missing,
            "directory_upload": self.upload.status(),
        }

    async def update(self, data: dict[str, Any], actor: dict[str, str] | None = None,
                     action: str = "ayarlar") -> dict[str, Any]:
        if self.store is None:
            raise ValueError("ayar ekranı salt okunur (LOCAL_SETTINGS_FILE boş)")
        async with self._lock:
            clean = self.normalize(data)
            changes = describe_changes(self.local, clean, self.rt.directory.index)
            before = self.rt.agent.texts
            await asyncio.to_thread(self.store.save, clean.to_json())
            self.local = clean
            self.apply(clean)
            missing = await self.rt.reload_prompts() if self.rt.agent.texts != before else []
            log.info("ayarlar kaydedildi: %d anons, %d kişiye takma ad", len(clean.texts), len(clean.aliases))
            if changes:
                a = actor or {"kim": "?", "rol": "?", "ip": "?"}
                try:
                    await asyncio.to_thread(self.rt.history.change_add, a["kim"], a["rol"], a["ip"], action, changes)
                except Exception:      # denetim kaydı yazılamadı: değişiklik yine geçerli, loglanır
                    log.exception("değişiklik geçmişi yazılamadı")
                log.info("değişiklik (%s, %s): %s", a["kim"], a["ip"], "; ".join(changes))
        view = self.view(_Who(actor))
        view["missing"] = missing
        return view


class _Who:
    def __init__(self, actor: dict[str, str] | None):
        self.name = (actor or {}).get("kim", "")
        self.role = (actor or {}).get("rol", "yonetici")


def describe_changes(old: LocalSettings, new: LocalSettings, index: Any) -> list[str]:
    """Değişiklik geçmişi için insan okunur özet: "Karşılama anonsu değişti", "takma ad eklendi: …"."""
    out: list[str] = []
    ot, nt = old.texts, new.texts
    for name in TEXT_FIELDS:
        if ot.get(name) != nt.get(name):
            label = TEXT_LABELS[name][0]
            out.append(f"{label} anonsu {'varsayılana döndü' if name not in nt else 'değişti'}: "
                       f"{nt.get(name, '')[:80]}".rstrip(": "))
    for key, label in (("office_hours", "çalışma saatleri"), ("holidays", "tatiller"),
                       ("after_hours_mode", "mesai dışı davranış")):
        if (v := getattr(new, key)) != getattr(old, key):
            out.append(f"{label}: {'(ayar dosyasındaki)' if v is None else v}")
    names = {c.id: c.label for c in (*old.contacts, *new.contacts)}

    def who(cid: str) -> str:
        c = index.get(cid)
        return names.get(cid) or (c.full_name if c else cid)

    oa, na = old.aliases, new.aliases
    for cid in sorted(set(oa) | set(na)):
        added = [a for a in na.get(cid, []) if a not in oa.get(cid, [])]
        removed = [a for a in oa.get(cid, []) if a not in na.get(cid, [])]
        if added:
            out.append(f"takma ad eklendi: {who(cid)} ← {', '.join(added)}")
        if removed:
            out.append(f"takma ad silindi: {who(cid)} ← {', '.join(removed)}")
    oc = {c.id: c for c in old.contacts}
    nc = {c.id: c for c in new.contacts}
    for cid in nc.keys() - oc.keys():
        out.append(f"yerel kayıt eklendi: {names[cid]} ({nc[cid].department or '-'}, {nc[cid].extension})")
    for cid in oc.keys() - nc.keys():
        out.append(f"yerel kayıt silindi: {names[cid]} ({oc[cid].extension})")
    return out


def _label_error(msg: str) -> str:
    for name, (label, _) in TEXT_LABELS.items():
        msg = msg.replace(f"{name.upper()}_TEXT", label)
    return msg


def _clean_aliases(values: Any) -> list[str]:
    """Bir kaydın takma adları: 2–60 harf, unvandan ibaret değil, tekrarsız, en çok MAX_ALIASES."""
    seen: set[str] = set()
    clean: list[str] = []
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
    return clean


def _clean_contact(raw: Any, taken: dict[str, str], names: set[str]) -> LocalContact:
    """Bir yerel kayıt: biçim, zorunlu alanlar, dahili çakışması. `taken` / `names` (dahili → ad, kişi adları)
    güncellenir: sonraki kayıtlar bununla da çakışamaz."""
    if not isinstance(raw, dict):
        raise ValueError("kayıt JSON nesnesi olmalı")
    c = {k: " ".join(str(raw.get(k) or "").split()) for k in
         ("first_name", "last_name", "department", "title", "extension")}
    c["kind"] = raw.get("kind") or "person"
    if c["kind"] not in ("person", "department"):
        raise ValueError("kayıt türü kişi ya da birim hattı olmalı")
    who = f"{c['first_name']} {c['last_name']}".strip() or c["department"]
    if any(len(v) > 60 for v in c.values()):
        raise ValueError(f"{who or 'kayıt'}: alanlar en çok 60 karakter")
    if c["kind"] == "person" and not (c["first_name"] and c["last_name"]):
        raise ValueError("kişi kaydında ad ve soyad gerekli")
    if c["kind"] == "department":
        if not c["department"]:
            raise ValueError("birim hattında birim adı gerekli")
        c["first_name"] = c["last_name"] = ""
    if not _EXTENSION.match(c["extension"]):
        raise ValueError(f"{who}: dahili numara 2–6 rakam olmalı (dış hat yazılamaz)")
    if c["extension"] in taken:
        raise ValueError(f"{who}: {c['extension']} dahilisi zaten {taken[c['extension']]} kaydında")
    if c["kind"] == "person" and search_key(who) in names:
        raise ValueError(f"{who} rehberde zaten var")
    cid = str(raw.get("id") or "")
    taken[c["extension"]] = who
    if c["kind"] == "person":
        names.add(search_key(who))
    return LocalContact(id=cid if re.fullmatch(r"yerel-[0-9a-f]{8}", cid) else f"yerel-{secrets.token_hex(4)}",
                        first_name=c["first_name"], last_name=c["last_name"], department=c["department"],
                        title=c["title"], extension=c["extension"],
                        kind="department" if c["kind"] == "department" else "person")
