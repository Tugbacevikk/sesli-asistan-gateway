"""Arayüz ve yönetim uçlarına erişim (tasarim.md 10.2, 13.1): kim, hangi rolle.

Roller:
  - yonetici: her şey (izleme, deneme çağrısı, ayar ve rehber değişikliği)
  - izleyici: izleme, deneme çağrısı, ayarları görme — değiştiremez

Anahtarlar `UI_USERS` ile kişiye bağlanır (secrets.env): "tugba:yonetici:<anahtar>, ali:izleyici:<anahtar>". Böylece
değişiklik geçmişinde değişikliği kimin yaptığı görünür. `UI_USERS` tanımlıysa santralin `API_KEY`'i yalnız
**izleyici** sayılır (Prometheus /metrics kazıması için), ayar değiştiremez. `UI_USERS` boşsa eski davranış:
`API_KEY` yönetici; o da boşsa (geliştirme) anahtarsız yerel erişim yönetici.
Her durumda önce `UI_ALLOWED_IPS` uygulanır.
"""
from __future__ import annotations

import hmac
from dataclasses import dataclass

ADMIN, VIEWER = "yonetici", "izleyici"
ROLES = (ADMIN, VIEWER)


@dataclass(frozen=True)
class Principal:
    name: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == ADMIN


def parse_users(spec: str) -> dict[str, Principal]:
    """'ad:rol:anahtar, …' → {anahtar: Principal}. Hatalıysa ValueError (anahtar mesajda gösterilmez)."""
    users: dict[str, Principal] = {}
    for i, item in enumerate(filter(None, (x.strip() for x in spec.split(","))), 1):
        parts = item.split(":", 2)
        if len(parts) != 3:
            raise ValueError(f"UI_USERS {i}. kayıt 'ad:rol:anahtar' biçiminde olmalı")
        name, role, key = (p.strip() for p in parts)
        if not name:
            raise ValueError(f"UI_USERS {i}. kayıtta ad boş")
        if role not in ROLES:
            raise ValueError(f"UI_USERS {name}: rol {' ya da '.join(ROLES)} olmalı")
        if len(key) < 16:
            raise ValueError(f"UI_USERS {name}: anahtar en az 16 karakter olmalı")
        if key in users:
            raise ValueError(f"UI_USERS {name}: aynı anahtar iki kişide")
        users[key] = Principal(name, role)
    return users


class UiAccess:
    def __init__(self, api_key: str = "", users: dict[str, Principal] | None = None,
                 allowed_ips: set[str] | None = None):
        self.api_key, self.users, self.allowed_ips = api_key, users or {}, allowed_ips

    def check(self, host: str, key: str) -> tuple[Principal | None, str]:
        """(kişi, "") ya da (None, ret nedeni)."""
        if self.allowed_ips is not None and host not in self.allowed_ips:
            return None, "kaynak adres izinli değil (UI_ALLOWED_IPS)"
        given = key.encode()
        if self.users:
            # hepsi karşılaştırılır (sabit süre): hangi anahtarın yakın olduğu sızmasın
            match = None
            for k, p in self.users.items():
                if hmac.compare_digest(given, k.encode()):
                    match = p
            if match is not None:
                return match, ""
            if self.api_key and hmac.compare_digest(given, self.api_key.encode()):
                return Principal("servis (API_KEY)", VIEWER), ""
            return None, "geçersiz ya da eksik anahtar"
        if self.api_key:
            if hmac.compare_digest(given, self.api_key.encode()):
                return Principal("yönetici (API_KEY)", ADMIN), ""
            return None, "geçersiz ya da eksik anahtar"
        return Principal("yerel", ADMIN), ""
