"""Sentetik şirket rehberi üretici (birim yollu, ADR-032): Satış, Muhasebe, İnsan Kaynakları, Bilgi İşlem gibi
birimleri ve alt birimleri olan orta ölçekli bir şirket. Ad ve soyadlar ortak Türkçe havuzdan, sabit tohumla
(`random.Random(2026)`) seçilir: her çalıştırmada aynı rehber çıkar. Gerçek bir kişi ya da kurumla ilgisi yoktur.

Rehberde bilerek bulunanlar (diyalog denemeleri için):
  - aynı ad farklı birimlerde ("Ayşe Hanım" → "hangisi?"), aynı unvan farklı birimlerde ("müdür"),
  - alt birimler (Bilgi İşlem → Yazılım / Sistem ve Ağ / Teknik Destek; Satış → Yurt İçi / Yurt Dışı / Kurumsal),
  - birimlerin eş anlamlıları ("İK", "personel", "BT", "çağrı merkezi", "depo"),
  - aktarılamayan makam (Genel Müdür: asistanına yönlendirilir).

Kullanım:
    python tools/rehber/sirket_rehber.py --cikti data/sirket_rehber.json
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

UPDATED_AT = "2026-11-10T08:00:00Z"

ERKEK = ["Ahmet", "Ali", "Burak", "Can", "Cem", "Emre", "Hakan", "Hasan", "Kemal", "Mehmet", "Murat", "Mustafa",
         "Okan", "Onur", "Ömer", "Serkan", "Tolga", "Uğur", "Volkan", "Yusuf"]
KADIN = ["Ayşe", "Büşra", "Ceren", "Deniz", "Derya", "Ebru", "Elif", "Esra", "Fatma", "Gamze", "Gizem", "Hilal",
         "İrem", "Merve", "Özlem", "Seda", "Selin", "Yasemin", "Zeynep", "Pınar"]
SOYAD = ["Acar", "Akın", "Arslan", "Aydın", "Bozkurt", "Bulut", "Çelik", "Demir", "Doğan", "Erdem", "Güneş", "Kaplan",
         "Kara", "Kaya", "Koç", "Korkmaz", "Kurt", "Özdemir", "Öztürk", "Polat", "Şahin", "Tekin", "Turan", "Uysal",
         "Yavuz", "Yıldırım", "Yıldız", "Yılmaz", "Aksoy", "Başaran", "Çetin", "Ekinci", "Güler", "Keskin", "Sevim"]

# (birim yolu, eş anlamlılar, [(unvan, kişi sayısı)], kategori). Birim hattı her birim için ayrıca eklenir.
BIRIMLER: list[tuple[tuple[str, ...], list[str], list[tuple[str, int]]]] = [
    (("Genel Müdürlük",), ["Yönetim"], [("Genel Müdür Yardımcısı", 2), ("Genel Müdür Asistanı", 1)]),
    (("Satış",), ["Satış Birimi", "Satış Departmanı"], [("Satış Müdürü", 1)]),
    (("Satış", "Yurt İçi Satış"), ["Yurtiçi Satış"], [("Bölge Satış Sorumlusu", 2),
                                                                     ("Satış Temsilcisi", 8)]),
    (("Satış", "Yurt Dışı Satış"), ["Dış Satış", "İhracat", "Yurtdışı Satış"], [("İhracat Uzmanı", 4)]),
    (("Satış", "Kurumsal Satış"), ["Kurumsal Müşteriler"], [("Kurumsal Müşteri Temsilcisi", 4)]),
    (("Pazarlama",), ["Reklam", "Kurumsal İletişim"], [("Pazarlama Müdürü", 1), ("Dijital Pazarlama Uzmanı", 2),
                                                        ("Ürün Müdürü", 2), ("Kurumsal İletişim Uzmanı", 1)]),
    (("Muhasebe",), ["Mali İşler", "Muhasebe Birimi"], [("Muhasebe Müdürü", 1), ("Muhasebe Uzmanı", 4),
                                                         ("Muhasebe Personeli", 2)]),
    (("Finans",), ["Finansman", "Tahsilat"], [("Finans Müdürü", 1), ("Finans Uzmanı", 2), ("Tahsilat Sorumlusu", 2)]),
    (("İnsan Kaynakları",), ["İK", "Personel", "Personel Birimi"], [("İnsan Kaynakları Müdürü", 1),
                                                                    ("İşe Alım Uzmanı", 2), ("Bordro Uzmanı", 2),
                                                                    ("Eğitim Uzmanı", 1)]),
    (("Bilgi İşlem",), ["Bilgi Teknolojileri", "BT"], [("Bilgi İşlem Müdürü", 1)]),
    (("Bilgi İşlem", "Yazılım Geliştirme"), ["Yazılım"], [("Yazılım Ekip Lideri", 1), ("Yazılım Uzmanı", 6)]),
    (("Bilgi İşlem", "Sistem ve Ağ"), ["Sistem", "Ağ Yönetimi"], [("Sistem Yöneticisi", 2), ("Ağ Uzmanı", 1)]),
    (("Bilgi İşlem", "Teknik Destek"), ["Yardım Masası", "Destek"], [("Teknik Destek Uzmanı", 5)]),
    (("Üretim",), ["Fabrika", "İmalat"], [("Üretim Müdürü", 1), ("Vardiya Amiri", 3), ("Üretim Planlama Uzmanı", 2),
                                          ("Bakım Sorumlusu", 2)]),
    (("Kalite",), ["Kalite Kontrol", "Kalite Güvence"], [("Kalite Müdürü", 1), ("Kalite Kontrol Uzmanı", 4)]),
    (("Ar-Ge",), ["Araştırma Geliştirme", "Arge"], [("Ar-Ge Müdürü", 1), ("Ar-Ge Mühendisi", 6)]),
    (("Satın Alma",), ["Tedarik", "Satınalma"], [("Satın Alma Müdürü", 1), ("Satın Alma Uzmanı", 4)]),
    (("Lojistik",), ["Depo", "Sevkiyat"], [("Lojistik Müdürü", 1), ("Depo Sorumlusu", 2), ("Sevkiyat Sorumlusu", 2)]),
    (("Müşteri Hizmetleri",), ["Çağrı Merkezi", "Müşteri Destek"], [("Müşteri Hizmetleri Müdürü", 1),
                                                                    ("Müşteri Temsilcisi", 8)]),
    (("Hukuk",), ["Hukuk Müşavirliği", "Avukatlık"], [("Hukuk Müşaviri", 1), ("Avukat", 2)]),
    (("İdari İşler",), ["İdari", "Güvenlik", "Resepsiyon"], [("İdari İşler Müdürü", 1), ("Güvenlik Amiri", 1),
                                                            ("Resepsiyon Görevlisi", 2)]),
]


def _record(cid: str, kind: str, path: tuple[str, ...], aliases: list[str], ext: str, *, first: str = "",
            last: str = "", title: str = "", own_aliases: list[str] | None = None, transferable: bool = True,
            category: str) -> dict[str, Any]:
    return {"id": cid, "kind": kind, "first_name": first, "last_name": last, "department": path[-1],
            "department_aliases": aliases, "title": title, "aliases": own_aliases or [], "extension": ext,
            "transferable": transferable, "birim_yolu": list(path), "kategori": category,
            "updated_at": UPDATED_AT, "deleted": False}


def build(seed: int = 2026) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    used: set[tuple[str, str]] = set()

    def name() -> tuple[str, str]:
        while True:
            first = rng.choice(rng.choice((ERKEK, KADIN)))
            last = rng.choice(SOYAD)
            if (first, last) not in used:          # aynı tam ad iki kez yok (ayırt edilemez)
                used.add((first, last))
                return first, last

    out: list[dict[str, Any]] = []
    top_no: dict[str, int] = {}                     # üst birim → sıra (dahili bloğu: 1100, 1200, …)
    sub_no: dict[str, int] = {}                     # üst birim → alt birim sayısı (blok içinde +20, +40, …)
    for path, aliases, staff in BIRIMLER:
        top = path[0]
        base = 1000 + 100 * top_no.setdefault(top, len(top_no) + 1)
        if len(path) > 1:
            sub_no[top] = sub_no.get(top, 0) + 1
            base += 20 * sub_no[top]
        slug = "-".join(p.lower().replace(" ", "")[:8] for p in path)
        out.append(_record(f"s-{slug}", "department", path, aliases, str(base),
                           category="birim" if len(path) == 1 else "alt birim"))
        n = base
        if path == ("Genel Müdürlük",):
            n += 1                                  # makam: aktarılmaz, asistanına yönlendirilir
            out.append(_record("s-genelmudur", "role", path, aliases, str(n), title="Genel Müdür",
                               own_aliases=["Genel Müdür Bey", "Patron"], transferable=False, category="makam"))
        for title, count in staff:
            for _ in range(count):
                n += 1
                first, last = name()
                out.append(_record(f"s-{n}", "person", path, aliases, str(n), first=first, last=last, title=title,
                                   category="kişi"))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cikti", default="data/sirket_rehber.json")
    args = ap.parse_args()
    contacts = build()
    exts = [c["extension"] for c in contacts]
    assert len(exts) == len(set(exts)), "aynı dahili iki kayıtta"
    doc = {"_aciklama": "Sentetik şirket rehberi (birim_yolu ve kategori alanlı); tools/rehber/sirket_rehber.py",
           "server_time": UPDATED_AT, "contacts": contacts}
    Path(args.cikti).write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    kinds: dict[str, int] = {}
    for c in contacts:
        kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1
    print(f"{args.cikti}: {len(contacts)} kayıt {kinds}")


if __name__ == "__main__":
    main()
