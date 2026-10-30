"""Çekirdek veri tipleri (tasarim.md 6.5, 6.6, 8)."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


@dataclass(frozen=True)
class Contact:
    """Rehber kaydı — Sentra Rehber API sözleşmesiyle aynı alanlar (tasarim.md 6.6)."""

    id: str
    first_name: str
    last_name: str
    department: str
    extension: str
    transferable: bool = True
    title: str = ""
    aliases: tuple[str, ...] = ()
    department_aliases: tuple[str, ...] = ()
    updated_at: str = ""
    deleted: bool = False
    kind: str = "person"          # "person" | "department" (birimin genel hattı)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip() or self.department

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> Contact:
        return cls(
            id=str(d["id"]), first_name=d.get("first_name", ""), last_name=d.get("last_name", ""),
            department=d.get("department", ""), extension=str(d.get("extension", "")),
            transferable=bool(d.get("transferable", True)), title=d.get("title") or "",
            aliases=tuple(d.get("aliases") or ()), department_aliases=tuple(d.get("department_aliases") or ()),
            updated_at=d.get("updated_at", ""), deleted=bool(d.get("deleted", False)),
            kind=d.get("kind") or "person",
        )


class Match(StrEnum):
    """InContacts sonucunun belirsizlik sınıfı; diyalog kuralları buna göre uygulanır (tasarim.md 7.5)."""

    SINGLE_HIGH = "single_high"   # tek sonuç, yüksek skor → aktar
    SINGLE_LOW = "single_low"     # tek sonuç, düşük skor → onay sor
    MULTIPLE = "multiple"         # birden çok aday → seçenek sor
    NONE = "none"                 # bulunamadı → tekrar sor


@dataclass(frozen=True)
class Candidate:
    contact: Contact
    score: int

    def to_tool(self) -> dict[str, Any]:
        c = self.contact
        return {"name": c.full_name, "department": c.department, "title": c.title,
                "extension": c.extension, "score": self.score}


@dataclass(frozen=True)
class SearchResult:
    candidates: tuple[Candidate, ...]
    match: Match


# --- Santrale gidecek eylemler (tasarim.md 6.2.2) ---

@dataclass(frozen=True)
class TransferAction:
    extension: str
    contact_id: str
    type: str = "transfer"


@dataclass(frozen=True)
class OperatorAction:
    reason: str
    type: str = "operator"


@dataclass(frozen=True)
class HangupAction:
    """Kapanış anonsundan sonra çağrıyı kapat (mesai dışı / tatil, tasarim.md 5.1.3)."""

    reason: str
    type: str = "hangup"


Action = TransferAction | OperatorAction | HangupAction


@dataclass
class DialogTurn:
    """Bir turun sonucu: seslendirilecek metin + santrale gönderilecek eylemler."""

    text: str
    actions: list[Action] = field(default_factory=list)
    ended: bool = False           # oturum bitti (aktarma başarılı)
    fallback: bool = False        # LLM yerine sabit metin kullanıldı (hata/kural)
    fast: bool = False            # LLM'siz hızlı yol: arayanın sözünde tam ad / birim net bulundu (ADR-016)
    llm_calls: int = 0            # bu turda kaç LLM çağrısı yapıldı (0: kodla çözüldü; metrik: yol oranı)
