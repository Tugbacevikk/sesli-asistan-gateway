"""Ayar ekranında kaydedilen yerel ayarlar (tasarim.md 13.1): ayar dosyasının (ortam değişkenleri) üzerine yazılan
fark. Yalnız ayar dosyasından farklı olan alanlar dolu; boş alan "ayar dosyasındaki değer geçerli" demektir.

Dosya biçimi (`LOCAL_SETTINGS_FILE`, JSON) değişmez: `to_json` yalnız dolu alanları yazar. Okuma ve doğrulama
`settings_service.normalize`'da (dosyadan okunan da istekten gelen de aynı kurallardan geçer)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

ContactKind = Literal["person", "department"]
AfterHoursMode = Literal["announce", "assistant"]


@dataclass(frozen=True)
class LocalContact:
    """Arayüzden eklenen rehber kaydı (kimliği `yerel-…`)."""

    id: str
    first_name: str
    last_name: str
    department: str
    title: str
    extension: str
    kind: ContactKind = "person"

    @property
    def label(self) -> str:
        """Değişiklik geçmişi ve hata mesajları için: kişinin adı ya da birim hattının birimi."""
        return f"{self.first_name} {self.last_name}".strip() or self.department

    def as_api(self) -> dict[str, Any]:
        """Rehber kaydı biçimi (`Contact.from_api`) ve dosyadaki biçim."""
        return asdict(self)


@dataclass(frozen=True)
class LocalSettings:
    texts: dict[str, str] = field(default_factory=dict)               # anons adı → metin
    office_hours: str | None = None
    holidays: str | None = None
    after_hours_mode: AfterHoursMode | None = None
    aliases: dict[str, list[str]] = field(default_factory=dict)       # kayıt kimliği → takma adlar
    contacts: tuple[LocalContact, ...] = ()

    def to_json(self) -> dict[str, Any]:
        """Dosyaya yazılan biçim: yalnız dolu alanlar."""
        out: dict[str, Any] = {}
        if self.texts:
            out["texts"] = dict(self.texts)
        for key in ("office_hours", "holidays", "after_hours_mode"):
            if (v := getattr(self, key)) is not None:
                out[key] = v
        if self.contacts:
            out["contacts"] = [c.as_api() for c in self.contacts]
        if self.aliases:
            out["aliases"] = {k: list(v) for k, v in self.aliases.items()}
        return out
