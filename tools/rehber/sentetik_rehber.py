"""Sentetik kurum rehberi üretici betik.

Makam hatlı büyük bir kamu kurumunu (Yeşilova Valiliği ve bağlı birimleri) taklit eden,
gerçek kurum verisi (data/yerel/, sample_db*.csv) içermeyen ve kamuya açık paylaşılabilecek
sentetik bir telefon rehberi ve santral dökümü üretir.

Kullanım:
    python tools/rehber/sentetik_rehber.py
    python tools/rehber/sentetik_rehber.py --cikti data/sentetik_kurum_rehber.json --tsv data/sentetik_kurum_rehber.tsv
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path
from typing import Any

# Kurum ve yer adları (uydurma)
IL_ADI = "Yeşilova"
ILCELER = ["Akçakaya", "Dereköy", "Taşlıca", "Ilıcapınar"]

# Ad ve soyad havuzu (yaygın Türkçe adlar)
ERKEK_ADLAR = [
    "Mehmet",
    "Mustafa",
    "Ahmet",
    "Ali",
    "Hüseyin",
    "Hasan",
    "İbrahim",
    "İsmail",
    "Osman",
    "Murat",
    "Ömer",
    "Ramazan",
    "Halil",
    "Süleyman",
    "Abdullah",
    "Mahmut",
    "Recep",
    "Salih",
    "Fatih",
    "Kadir",
    "Emre",
    "Hakan",
    "Adem",
    "Kemal",
    "Yusuf",
    "Can",
    "Burak",
    "Serkan",
    "Oğuz",
    "Tolga",
    "Cem",
    "Deniz",
    "Onur",
    "Gökhan",
    "Eren",
    "Kaan",
    "Barış",
    "Umut",
    "Volkan",
    "Erhan",
    "Sinan",
    "Tarkan",
    "Metin",
    "Cihan",
    "Levent",
    "Zafer",
    "İlhan",
]

KADIN_ADLAR = [
    "Fatma",
    "Ayşe",
    "Emine",
    "Hatice",
    "Zeynep",
    "Meryem",
    "Şerife",
    "Zehra",
    "Sultan",
    "Hanife",
    "Merve",
    "Havva",
    "Fadime",
    "Elif",
    "Özlem",
    "Yasemin",
    "Hülya",
    "Dilek",
    "Filiz",
    "Esra",
    "Büşra",
    "Gülşen",
    "Tuğba",
    "Ebru",
    "Seda",
    "Sevgi",
    "Derya",
    "Gözde",
    "Burcu",
    "Gamze",
    "Gizem",
    "İrem",
    "Pelin",
    "Ece",
    "Damla",
    "Melis",
    "Duygu",
    "Aslı",
    "Sinem",
    "Ceren",
    "Bahar",
    "Sevil",
    "Pınar",
    "Eda",
    "Melike",
]

SOYADLAR = [
    "Yılmaz",
    "Kaya",
    "Demir",
    "Çelik",
    "Şahin",
    "Yıldız",
    "Yıldırım",
    "Öztürk",
    "Aydın",
    "Özdemir",
    "Arslan",
    "Doğan",
    "Kılıç",
    "Aslan",
    "Çetin",
    "Kara",
    "Koç",
    "Kurt",
    "Özkan",
    "Şimşek",
    "Polat",
    "Özcan",
    "Korkmaz",
    "Çakır",
    "Erdoğan",
    "Yavuz",
    "Güneş",
    "Acar",
    "Aksoy",
    "Yüksel",
    "Güler",
    "Yalçın",
    "Gül",
    "Güngör",
    "Bulut",
    "Keskin",
    "Bozkurt",
    "Turan",
    "Ünal",
    "Avcı",
    "Duran",
    "Sarı",
    "Yurt",
    "Coşkun",
    "Ceylan",
]

UNVANLAR_IDARI = [
    "Şef",
    "Veri Hazırlama Kontrol İşletmeni",
    "Memur",
    "Bilgisayar İşletmeni",
    "Santral Memuru",
    "Sekreter",
    "Arşiv Memuru",
    "Evrak Memuru",
]

UNVANLAR_TEKNIK = [
    "Mühendis",
    "Tekniker",
    "Teknisyen",
    "Programcı",
    "Çözümleyici",
]

UNVANLAR_UZMANLIK = [
    "Uzman",
    "Uzman Yardımcısı",
    "Sosyal Çalışmacı",
    "Psikolog",
    "Tabip",
    "Veteriner Hekim",
    "Ziraat Mühendisi",
    "Avukat",
]

UNVANLAR_ASAYIS_JANDARMA = [
    "Astsubay",
    "Kıdemli Astsubay",
    "Uzman Jandarma",
    "Uzman Çavuş",
]

UNVANLAR_ASAYIS_EMNIYET = [
    "Polis Memuru",
    "Başpolis Memuru",
    "Komiser Yardımcısı",
    "Komiser",
]

TIMESTAMP = "2026-11-09T08:00:00Z"


def build_synthetic_directory(target_count: int = 600, seed: int = 2026) -> list[dict[str, Any]]:
    """Belirlenen tohumla tekrarlanabilir sentetik kurum rehberi üretir."""
    rng = random.Random(seed)
    contacts: list[dict[str, Any]] = []
    used_extensions: set[str] = set()

    def add_contact(
        cid: str,
        kind: str,
        first: str,
        last: str,
        dept: str,
        title: str,
        ext: str,
        transferable: bool = True,
        deleted: bool = False,
        aliases: list[str] | None = None,
        dept_aliases: list[str] | None = None,
    ) -> dict[str, Any]:
        if ext in used_extensions:
            raise ValueError(f"Dahili çakışması: {ext}")
        used_extensions.add(ext)
        rec = {
            "id": cid,
            "kind": kind,
            "first_name": first,
            "last_name": last,
            "department": dept,
            "department_aliases": dept_aliases or [],
            "title": title,
            "aliases": aliases or [],
            "extension": ext,
            "transferable": transferable,
            "updated_at": TIMESTAMP,
            "deleted": deleted,
        }
        contacts.append(rec)
        return rec

    # -------------------------------------------------------------------------
    # 1. YEŞİLOVA VALİLİĞİ (20000 - 20099)
    # -------------------------------------------------------------------------
    valilik_dept = f"{IL_ADI} Valiliği"
    valilik_aliases = ["Valilik", f"{IL_ADI} Valilik", "Valilik Makamı"]
    add_contact(
        "valilik-genel",
        "department",
        "",
        "",
        valilik_dept,
        "Birim hattı",
        "20000",
        dept_aliases=valilik_aliases,
    )
    # Vali (aktarılamaz!)
    add_contact(
        "valilik-vali",
        "role",
        "",
        "",
        valilik_dept,
        "Vali",
        "20001",
        transferable=False,
        aliases=[f"{IL_ADI} Valisi"],
        dept_aliases=valilik_aliases,
    )
    # 3 Vali Yardımcısı (kişili)
    add_contact(
        "valilik-vali-yrd-1",
        "person",
        "Kemal",
        "Yılmaz",
        valilik_dept,
        "Vali Yardımcısı",
        "20002",
        aliases=["Kemal Bey"],
        dept_aliases=valilik_aliases,
    )
    add_contact(
        "valilik-vali-yrd-2",
        "person",
        "Fatma",
        "Kaya",
        valilik_dept,
        "Vali Yardımcısı",
        "20003",
        aliases=["Fatma Hanım"],
        dept_aliases=valilik_aliases,
    )
    add_contact(
        "valilik-vali-yrd-3",
        "person",
        "Murat",
        "Şahin",
        valilik_dept,
        "Vali Yardımcısı",
        "20004",
        dept_aliases=valilik_aliases,
    )
    # Özel Kalem Müdürlüğü
    ozel_kalem_dept = "Özel Kalem Müdürlüğü"
    add_contact(
        "valilik-ozel-kalem-md",
        "role",
        "",
        "",
        ozel_kalem_dept,
        "Özel Kalem Müdürü",
        "20010",
        aliases=["Özel Kalem", "Valilik Özel Kalem"],
        dept_aliases=["Özel Kalem", "Kalem"],
    )
    add_contact(
        "valilik-ozel-kalem-memur",
        "person",
        "Zeynep",
        "Çelik",
        ozel_kalem_dept,
        "Özel Kalem Memuru",
        "20011",
    )
    # Belgegeçer hattı (aktarılamaz!)
    add_contact(
        "valilik-belgegecer",
        "role",
        "",
        "",
        valilik_dept,
        "Valilik Belgegeçer",
        "20099",
        transferable=False,
    )

    # Valilik Hukuk İşleri Şube Müdürlüğü (Alt hatlar + Aynı unvan iki hat!)
    hukuk_dept = "Valilik Hukuk İşleri Şube Müdürlüğü"
    add_contact("valilik-hukuk-sb-md", "role", "", "", hukuk_dept, "Şube Müdürü", "20020")
    # Alt cihaz hatları (öz alt küme olanlar: Shp., Mbl., Dinlenme)
    add_contact("valilik-hukuk-sb-md-shp", "role", "", "", hukuk_dept, "Şube Müdürü Shp.", "20021")
    add_contact("valilik-hukuk-sb-md-mbl", "role", "", "", hukuk_dept, "Şube Müdürü Mbl.", "20022")
    add_contact("valilik-hukuk-sb-md-dinl", "role", "", "", hukuk_dept, "Şube Müdürü Dinlenme", "20023")
    add_contact("valilik-hukuk-musaviri", "role", "", "", hukuk_dept, "Hukuk Müşaviri Avukat", "20024")
    # AYNI UNVAN, AYNI BİRİM: Ayırt edilemeyen iki hat (operatöre gitmeli)
    add_contact("valilik-hukuk-avukat-1", "role", "", "", hukuk_dept, "Avukat", "20025")
    add_contact("valilik-hukuk-avukat-2", "role", "", "", hukuk_dept, "Avukat", "20026")

    # İl Yazı İşleri Müdürlüğü
    yazi_dept = "İl Yazı İşleri Müdürlüğü"
    add_contact("valilik-yazi-md", "role", "", "", yazi_dept, "Yazı İşleri Müdürü", "20030")

    # Basın ve Halkla İlişkiler Şube Müdürlüğü
    basin_dept = "İl Basın ve Halkla İlişkiler Şube Müdürlüğü"
    add_contact("valilik-basin-sb-md", "role", "", "", basin_dept, "Şube Müdürü", "20040")

    # İl Planlama ve Koordinasyon Müdürlüğü
    plan_dept = "İl Planlama ve Koordinasyon Müdürlüğü"
    add_contact("valilik-plan-md", "role", "", "", plan_dept, "İl Planlama Müdürü", "20050")

    # 112 Acil Çağrı Merkezi Müdürlüğü
    cagri_dept = "112 Acil Çağrı Merkezi Müdürlüğü"
    add_contact("valilik-112-md", "role", "", "", cagri_dept, "112 Müdürü", "20060")

    # -------------------------------------------------------------------------
    # 2. YEŞİLOVA İL JANDARMA KOMUTANLIĞI (30000 - 34999)
    # -------------------------------------------------------------------------
    jandarma_dept = f"{IL_ADI} İl Jandarma Komutanlığı"
    jandarma_aliases = ["İl Jandarma", "Jandarma", "Jandarma Komutanlığı"]
    add_contact(
        "jandarma-genel",
        "department",
        "",
        "",
        jandarma_dept,
        "Birim hattı",
        "30000",
        dept_aliases=jandarma_aliases,
    )
    add_contact(
        "jandarma-komutan",
        "role",
        "",
        "",
        jandarma_dept,
        "İl Jandarma Komutanı",
        "30001",
        aliases=["Jandarma Komutanı"],
        dept_aliases=jandarma_aliases,
    )
    add_contact(
        "jandarma-komutan-yrd",
        "role",
        "",
        "",
        jandarma_dept,
        "İl Jandarma Komutan Yardımcısı",
        "30002",
        aliases=["Jandarma Komutan Yardımcısı"],
        dept_aliases=jandarma_aliases,
    )
    add_contact("jandarma-kurmay-bsk", "role", "", "", jandarma_dept, "Kurmay Başkanı", "30003")
    add_contact(
        "jandarma-belgegecer",
        "role",
        "",
        "",
        jandarma_dept,
        "İl Jandarma Belgegeçer",
        "30009",
        transferable=False,
    )

    # Nöbetçi Amiri ve Nöbetçi Astsubayı (Kişi adı gibi görünen ama kişi olmayan hatlar)
    add_contact("jandarma-nobetci-amiri", "role", "", "", jandarma_dept, "Nöbetçi Amiri", "30080")
    add_contact("jandarma-nobetci-asb", "role", "", "", jandarma_dept, "Nöbetçi Astsubayı", "30081")

    # Jandarma Şubeleri
    jandarma_subeler = [
        ("Asayiş", "30100", ["Jandarma Asayiş", "Asayiş Şube"]),
        ("Personel", "30200", ["Jandarma Personel", "Personel Şube"]),
        ("Lojistik", "30300", ["Jandarma Lojistik", "Lojistik Şube"]),
        ("Harekat", "30400", ["Jandarma Harekat", "Harekat Şube"]),
        ("Kaçakçılık ve Organize Suçlarla Mücadele", "30500", ["Jandarma KOM", "KOM Şube", "Kaçakçılık Şube"]),
        ("Siber Suçlarla Mücadele", "30600", ["Jandarma Siber", "Siber Şube"]),
        ("Trafik", "30700", ["Jandarma Trafik", "Trafik Şube"]),
    ]

    for sube_adi, base_ext_str, aliases in jandarma_subeler:
        sube_dept = f"İl Jandarma {sube_adi} Şube Müdürlüğü"
        base_int = int(base_ext_str)
        # Şube Müdürü (Her birinde aynı unvan!)
        add_contact(
            f"j-{sube_adi.lower()}-md", "role", "", "", sube_dept, "Şube Müdürü", str(base_int), dept_aliases=aliases
        )
        # Asayiş şubede alt cihaz hatları
        if sube_adi == "Asayiş":
            add_contact("j-asy-md-shp", "role", "", "", sube_dept, "Şube Müdürü Shp.", str(base_int + 1))
            add_contact("j-asy-md-mbl", "role", "", "", sube_dept, "Şube Müdürü Mbl.", str(base_int + 2))
            add_contact("j-asy-md-dinl", "role", "", "", sube_dept, "Şube Müdürü Dinlenme", str(base_int + 3))

        # İdari İşlemler Astsubayı (En az 5 birimde aynı unvan!)
        add_contact(
            f"j-{sube_adi.lower()}-idari-asb",
            "role",
            "",
            "",
            sube_dept,
            "İdari İşlemler Astsubayı",
            str(base_int + 10),
            dept_aliases=aliases,
        )
        # Büro Amiri (Çok birimde aynı unvan!)
        add_contact(
            f"j-{sube_adi.lower()}-buro-amiri",
            "role",
            "",
            "",
            sube_dept,
            "Büro Amiri",
            str(base_int + 20),
            dept_aliases=aliases,
        )

    # Aynı ad soyadlı kişi 1 (Jandarma'daki Can Yurt):
    add_contact(
        "jandarma-can-yurt",
        "person",
        "Can",
        "Yurt",
        "İl Jandarma Asayiş Şube Müdürlüğü",
        "Astsubay",
        "30150",
    )

    # -------------------------------------------------------------------------
    # 3. YEŞİLOVA İL EMNİYET MÜDÜRLÜĞÜ (40000 - 44999)
    # -------------------------------------------------------------------------
    emniyet_dept = f"{IL_ADI} İl Emniyet Müdürlüğü"
    emniyet_aliases = ["İl Emniyet", "Emniyet", "Emniyet Müdürlüğü", "Polis"]
    add_contact(
        "emniyet-genel",
        "department",
        "",
        "",
        emniyet_dept,
        "Birim hattı",
        "40000",
        dept_aliases=emniyet_aliases,
    )
    add_contact(
        "emniyet-mudur",
        "role",
        "",
        "",
        emniyet_dept,
        "İl Emniyet Müdürü",
        "40001",
        aliases=["Emniyet Müdürü"],
        dept_aliases=emniyet_aliases,
    )
    add_contact("emniyet-mudur-yrd-1", "role", "", "", emniyet_dept, "İl Emniyet Müdür Yardımcısı", "40002")
    add_contact("emniyet-mudur-yrd-2", "role", "", "", emniyet_dept, "İl Emniyet Müdür Yardımcısı", "40003")
    add_contact(
        "emniyet-belgegecer", "role", "", "", emniyet_dept, "İl Emniyet Belgegeçer", "40009", transferable=False
    )

    emniyet_subeler = [
        ("Asayiş", "40100", ["Emniyet Asayiş"]),
        ("Trafik Denetleme", "40200", ["Emniyet Trafik", "Trafik Şube", "Trafik Tescil"]),
        ("Narkotik Suçlarla Mücadele", "40300", ["Emniyet Narkotik", "Narkotik Şube", "Narkotik"]),
        ("Terörle Mücadele", "40400", ["Emniyet TEM", "TEM Şube"]),
        ("Siber Suçlarla Mücadele", "40500", ["Emniyet Siber", "Siber Şube"]),
        ("Güvenlik", "40600", ["Emniyet Güvenlik"]),
        ("Çevik Kuvvet", "40700", ["Çevik Kuvvet"]),
    ]

    for sube_adi, base_ext_str, aliases in emniyet_subeler:
        sube_dept = f"İl Emniyet {sube_adi} Şube Müdürlüğü"
        base_int = int(base_ext_str)
        add_contact(
            f"e-{sube_adi.lower()}-md", "role", "", "", sube_dept, "Şube Müdürü", str(base_int), dept_aliases=aliases
        )
        add_contact(
            f"e-{sube_adi.lower()}-buro-amiri",
            "role",
            "",
            "",
            sube_dept,
            "Büro Amiri",
            str(base_int + 50),
            dept_aliases=aliases,
        )

    # Özel Büro Amirleri (Asayiş şube içinde)
    add_contact("e-cinayet-buro", "role", "", "", "İl Emniyet Asayiş Şube Müdürlüğü", "Cinayet Büro Amiri", "40110")
    add_contact("e-hirsizlik-buro", "role", "", "", "İl Emniyet Asayiş Şube Müdürlüğü", "Hırsızlık Büro Amiri", "40120")
    add_contact("e-ahlak-buro", "role", "", "", "İl Emniyet Asayiş Şube Müdürlüğü", "Ahlak Büro Amiri", "40130")
    add_contact("e-gasp-buro", "role", "", "", "İl Emniyet Asayiş Şube Müdürlüğü", "Gasp Büro Amiri", "40140")

    # Trafik Büro Amirleri
    add_contact(
        "e-tescil-buro", "role", "", "", "İl Emniyet Trafik Denetleme Şube Müdürlüğü", "Tescil Büro Amiri", "40210"
    )
    add_contact(
        "e-denetleme-buro",
        "role",
        "",
        "",
        "İl Emniyet Trafik Denetleme Şube Müdürlüğü",
        "Denetleme Büro Amiri",
        "40220",
    )

    # -------------------------------------------------------------------------
    # 4. DÖRT İLÇE KAYMAKAMLIĞI (50000 - 53999)
    # -------------------------------------------------------------------------
    ilceler_kayitlar = [
        ("Akçakaya", 50000, "Mehmet", "Demir", ["Mehmet Bey"]),
        ("Dereköy", 51000, "Ali", "Yılmaz", ["Ali Bey"]),
        ("Taşlıca", 52000, "Hüseyin", "Kaya", ["Hüseyin Bey"]),
        ("Ilıcapınar", 53000, "Ayşe", "Öztürk", ["Ayşe Hanım"]),
    ]

    for ilce, base_ext, k_first, k_last, k_aliases in ilceler_kayitlar:
        k_dept = f"{ilce} Kaymakamlığı"
        add_contact(
            f"{ilce.lower()}-genel",
            "department",
            "",
            "",
            k_dept,
            "Birim hattı",
            str(base_ext),
            dept_aliases=[ilce],
        )
        # Kaymakam (kişili, hem unvanla hem adla aranabilir)
        add_contact(
            f"{ilce.lower()}-kaymakam",
            "person",
            k_first,
            k_last,
            k_dept,
            "Kaymakam",
            str(base_ext + 1),
            aliases=[*k_aliases, f"{ilce} Kaymakamı"],
            dept_aliases=[ilce],
        )
        # Kalem ve Yazı İşleri
        add_contact(
            f"{ilce.lower()}-yazi-md",
            "role",
            "",
            "",
            f"{ilce} Yazı İşleri Müdürlüğü",
            "Yazı İşleri Müdürü",
            str(base_ext + 10),
        )
        add_contact(f"{ilce.lower()}-kalem", "role", "", "", k_dept, "Özel Kalem", str(base_ext + 20))
        add_contact(
            f"{ilce.lower()}-nufus-md",
            "role",
            "",
            "",
            f"{ilce} İlçe Nüfus Müdürlüğü",
            "İlçe Nüfus Müdürü",
            str(base_ext + 30),
        )
        add_contact(
            f"{ilce.lower()}-belgegecer",
            "role",
            "",
            "",
            k_dept,
            f"{ilce} Belgegeçer",
            str(base_ext + 99),
            transferable=False,
        )

    # -------------------------------------------------------------------------
    # 5. İL MÜDÜRLÜKLERİ (60000 - 69999)
    # -------------------------------------------------------------------------
    # Milli Eğitim
    meb_dept = f"{IL_ADI} İl Milli Eğitim Müdürlüğü"
    meb_aliases = ["İl Milli Eğitim", "Milli Eğitim", "Milli Eğitim Müdürlüğü", "Meb"]
    add_contact("meb-genel", "department", "", "", meb_dept, "Birim hattı", "60000", dept_aliases=meb_aliases)
    add_contact("meb-mudur", "role", "", "", meb_dept, "İl Milli Eğitim Müdürü", "60001", dept_aliases=meb_aliases)
    add_contact("meb-mudur-yrd", "role", "", "", meb_dept, "İl Milli Eğitim Müdür Yardımcısı", "60002")

    # Can Yurt 2 (Milli Eğitim'deki Can Yurt - AYNI AD SOYAD ÇAKIŞMASI!)
    add_contact("meb-can-yurt", "person", "Can", "Yurt", meb_dept, "Eğitim Uzmanı", "60050")

    meb_subeler = [
        "Temel Eğitim",
        "Ortaöğretim",
        "Mesleki Eğitim",
        "İnsan Kaynakları",
        "Destek Hizmetleri",
        "Strateji Geliştirme",
    ]
    for i, s_adi in enumerate(meb_subeler):
        add_contact(
            f"meb-sb-{i + 1}",
            "role",
            "",
            "",
            f"İl Milli Eğitim {s_adi} Şube Müdürlüğü",
            "Şube Müdürü",
            str(60100 + i * 10),
        )

    # Sağlık
    saglik_dept = f"{IL_ADI} İl Sağlık Müdürlüğü"
    saglik_aliases = ["İl Sağlık", "Sağlık", "Sağlık Müdürlüğü"]
    add_contact("saglik-genel", "department", "", "", saglik_dept, "Birim hattı", "62000", dept_aliases=saglik_aliases)
    add_contact("saglik-mudur", "role", "", "", saglik_dept, "İl Sağlık Müdürü", "62001", dept_aliases=saglik_aliases)
    add_contact("saglik-halk-bsk", "role", "", "", saglik_dept, "Halk Sağlığı Hizmetleri Başkanı", "62010")
    add_contact("saglik-kamu-bsk", "role", "", "", saglik_dept, "Kamu Hastaneleri Hizmetleri Başkanı", "62020")
    add_contact(
        "saglik-sb-1", "role", "", "", "İl Sağlık Acil Sağlık Hizmetleri Şube Müdürlüğü", "Şube Müdürü", "62100"
    )
    add_contact("saglik-sb-2", "role", "", "", "İl Sağlık Bulaşıcı Hastalıklar Şube Müdürlüğü", "Şube Müdürü", "62200")

    # Tarım ve Orman
    tarim_dept = f"{IL_ADI} İl Tarım ve Orman Müdürlüğü"
    tarim_aliases = ["İl Tarım", "Tarım", "Tarım ve Orman", "Tarım Müdürlüğü"]
    add_contact("tarim-genel", "department", "", "", tarim_dept, "Birim hattı", "64000", dept_aliases=tarim_aliases)
    add_contact(
        "tarim-mudur", "role", "", "", tarim_dept, "İl Tarım ve Orman Müdürü", "64001", dept_aliases=tarim_aliases
    )
    add_contact("tarim-sb-1", "role", "", "", "İl Tarım Bitkisel Üretim Şube Müdürlüğü", "Şube Müdürü", "64100")
    add_contact("tarim-sb-2", "role", "", "", "İl Tarım Hayvan Sağlığı Şube Müdürlüğü", "Şube Müdürü", "64200")
    add_contact("tarim-sb-3", "role", "", "", "İl Tarım Gıda ve Yem Şube Müdürlüğü", "Şube Müdürü", "64300")

    # Aile ve Sosyal Hizmetler
    aile_dept = f"{IL_ADI} Aile ve Sosyal Hizmetler İl Müdürlüğü"
    aile_aliases = ["Aile ve Sosyal Hizmetler", "Aile Müdürlüğü", "Sosyal Hizmetler"]
    add_contact("aile-genel", "department", "", "", aile_dept, "Birim hattı", "66000", dept_aliases=aile_aliases)
    add_contact("aile-mudur", "role", "", "", aile_dept, "İl Müdürü", "66001", dept_aliases=aile_aliases)
    add_contact("aile-sb-1", "role", "", "", "Aile Çocuk Hizmetleri Şube Müdürlüğü", "Şube Müdürü", "66100")
    add_contact("aile-sb-2", "role", "", "", "Aile Engelli ve Yaşlı Hizmetleri Şube Müdürlüğü", "Şube Müdürü", "66200")

    # -------------------------------------------------------------------------
    # 6. DESTEK VE ORTAK HATLAR (70000 - 70999)
    # -------------------------------------------------------------------------
    destek_dept = f"{IL_ADI} Valiliği Destek Hizmetleri"
    add_contact("destek-danisma", "role", "", "", destek_dept, "Danışma", "70001")
    add_contact("destek-santral", "role", "", "", destek_dept, "Santral", "70002")
    add_contact("destek-nizamiye", "role", "", "", destek_dept, "Nizamiye", "70003")
    add_contact("destek-yemekhane", "role", "", "", destek_dept, "Yemekhane", "70004")
    add_contact("destek-bilgi-islem", "role", "", "", destek_dept, "Bilgi İşlem", "70005")
    add_contact("destek-arsiv", "role", "", "", destek_dept, "Arşiv", "70006")
    # Kişi adı gibi görünen ama kişi olmayan hatlar:
    add_contact("destek-salon-sorumlusu", "role", "", "", destek_dept, "Salon Sorumlusu", "70007")
    add_contact("destek-nobetci-amiri", "role", "", "", destek_dept, "Nöbetçi Amiri", "70008")
    add_contact("destek-garaj-amiri", "role", "", "", destek_dept, "Garaj Amiri", "70009")

    # -------------------------------------------------------------------------
    # 7. SİLİNMİŞ KAYITLAR (TAM 5 TANE - deleted=True)
    # -------------------------------------------------------------------------
    add_contact("silinmis-1", "person", "Burak", "Çelik", valilik_dept, "Eski Uzman", "80001", deleted=True)
    add_contact("silinmis-2", "person", "Selin", "Demir", jandarma_dept, "Eski Memur", "80002", deleted=True)
    add_contact("silinmis-3", "person", "Emre", "Yıldız", emniyet_dept, "Eski Şef", "80003", deleted=True)
    add_contact("silinmis-4", "role", "", "", destek_dept, "Eski Danışma Hattı", "80004", deleted=True)
    add_contact(
        "silinmis-5", "department", "", "", "Mülga İl Koordinasyon Kurulu", "Birim hattı", "80005", deleted=True
    )

    # -------------------------------------------------------------------------
    # 8. KALAN KAYITLAR: DENGELİ DAĞITILMIŞ PERSONEL (~500 kişi)
    # -------------------------------------------------------------------------
    # Birim havuzu ve kapasite ağırlıkları
    birim_havuzu = [
        (valilik_dept, UNVANLAR_IDARI + UNVANLAR_UZMANLIK, 20100, 35),
        ("İl Jandarma Asayiş Şube Müdürlüğü", UNVANLAR_ASAYIS_JANDARMA, 31000, 30),
        ("İl Jandarma Personel Şube Müdürlüğü", UNVANLAR_ASAYIS_JANDARMA + UNVANLAR_IDARI, 31100, 20),
        ("İl Jandarma Lojistik Şube Müdürlüğü", UNVANLAR_ASAYIS_JANDARMA + UNVANLAR_TEKNIK, 31200, 25),
        ("İl Jandarma Harekat Şube Müdürlüğü", UNVANLAR_ASAYIS_JANDARMA, 31300, 20),
        ("İl Jandarma Kaçakçılık ve Organize Suçlarla Mücadele Şube Müdürlüğü", UNVANLAR_ASAYIS_JANDARMA, 31400, 25),
        ("İl Jandarma Trafik Şube Müdürlüğü", UNVANLAR_ASAYIS_JANDARMA, 31500, 20),
        ("İl Emniyet Asayiş Şube Müdürlüğü", UNVANLAR_ASAYIS_EMNIYET, 41000, 40),
        ("İl Emniyet Trafik Denetleme Şube Müdürlüğü", UNVANLAR_ASAYIS_EMNIYET, 41100, 30),
        ("İl Emniyet Narkotik Suçlarla Mücadele Şube Müdürlüğü", UNVANLAR_ASAYIS_EMNIYET, 41200, 25),
        ("İl Emniyet Terörle Mücadele Şube Müdürlüğü", UNVANLAR_ASAYIS_EMNIYET, 41300, 25),
        ("İl Emniyet Çevik Kuvvet Şube Müdürlüğü", UNVANLAR_ASAYIS_EMNIYET, 41400, 35),
        ("Akçakaya Kaymakamlığı", UNVANLAR_IDARI, 50100, 20),
        ("Dereköy Kaymakamlığı", UNVANLAR_IDARI, 51100, 20),
        ("Taşlıca Kaymakamlığı", UNVANLAR_IDARI, 52100, 20),
        ("Ilıcapınar Kaymakamlığı", UNVANLAR_IDARI, 53100, 20),
        (meb_dept, UNVANLAR_IDARI + UNVANLAR_UZMANLIK, 60200, 40),
        (saglik_dept, UNVANLAR_IDARI + UNVANLAR_UZMANLIK, 62400, 45),
        (tarim_dept, UNVANLAR_IDARI + UNVANLAR_UZMANLIK, 64400, 35),
        (aile_dept, UNVANLAR_IDARI + UNVANLAR_UZMANLIK, 66400, 30),
    ]

    p_idx = 1
    # Ad çakışmalarını garantilemek için bilinçli 3 "Ayşe" ve 3 "Mehmet" eklenir
    ozel_adlar = [
        ("Ayşe", "Kaya", valilik_dept, "Uzman"),
        ("Ayşe", "Demir", meb_dept, "Şef"),
        ("Ayşe", "Çelik", saglik_dept, "Tabip"),
        ("Mehmet", "Şahin", jandarma_dept, "Astsubay"),
        ("Mehmet", "Yıldırım", emniyet_dept, "Polis Memuru"),
    ]
    for fn, ln, d, t in ozel_adlar:
        ext = str(71000 + p_idx)
        add_contact(f"p-ozel-{p_idx}", "person", fn, ln, d, t, ext)
        p_idx += 1

    for b_dept, b_unvanlar, ext_start, quota in birim_havuzu:
        for q in range(quota):
            if len(contacts) >= target_count:
                break
            is_erkek = rng.random() > 0.5
            fn = rng.choice(ERKEK_ADLAR if is_erkek else KADIN_ADLAR)
            ln = rng.choice(SOYADLAR)
            title = rng.choice(b_unvanlar)
            ext = str(ext_start + q)
            # Extension benzersizliğini garanti et
            while ext in used_extensions:
                ext_start += 1
                ext = str(ext_start + q)
            add_contact(f"p-gen-{len(contacts) + 1}", "person", fn, ln, b_dept, title, ext)

    # Hedef sayıya tamamlama
    while len(contacts) < target_count:
        is_erkek = rng.random() > 0.5
        fn = rng.choice(ERKEK_ADLAR if is_erkek else KADIN_ADLAR)
        ln = rng.choice(SOYADLAR)
        b_dept, b_unvanlar, _, _ = rng.choice(birim_havuzu)
        title = rng.choice(b_unvanlar)
        ext_int = 72000 + len(contacts)
        ext = str(ext_int)
        while ext in used_extensions:
            ext_int += 1
            ext = str(ext_int)
        add_contact(f"p-gen-{len(contacts) + 1}", "person", fn, ln, b_dept, title, ext)

    return contacts


# TSV Kısaltma Sözlüğü (Santral dökümü için)
# Bu sözlükte kısaltmalar tests/data/makam_rehber.tsv ile uyumlu yapılır.
TSV_UNVAN_KISALTMALARI = {
    "Şube Müdürü": "Şb.Md.",
    "Şube Müdürü Shp.": "Şb.Md. Shp.",
    "Şube Müdürü Mbl.": "Şb.Md. Mbl.",
    "Şube Müdürü Dinlenme": "Şb.Md. Dinl.",
    "İdari İşlemler Astsubayı": "İd.İşl.Asb.",
    "Büro Amiri": "Büro Amiri",
    "İl Jandarma Komutanı": "İl J.K.",
    "İl Jandarma Komutan Yardımcısı": "İl J.K.Yrd.",
    "Kurmay Başkanı": "Krm.Bşk.",
    "İl Emniyet Müdürü": "İl Emn.Md.",
    "İl Emniyet Müdür Yardımcısı": "İl Emn.Md.Yrd.",
    "Özel Kalem Müdürü": "Özl.Klm.Md.",
    "Özel Kalem": "Özl.Klm.",
    "Yazı İşleri Müdürü": "Yaz.İşl.Md.",
    "Valilik Belgegeçer": "Valilik Belgegeçer",
    "İl Jandarma Belgegeçer": "İl J. Belgegeçer",
    "İl Emniyet Belgegeçer": "İl Emn. Belgegeçer",
    "Nöbetçi Amiri": "Nöb.Amiri",
    "Nöbetçi Astsubayı": "Nöb.Asb.",
    "Salon Sorumlusu": "Sln.Sorumlusu",
    "Danışma": "Dnş.",
    "Santral": "Snt.",
    "Nizamiye": "Nizamiye",
    "Yemekhane": "Yemekhane",
    "Bilgi İşlem": "Bil.İşl.",
    "Arşiv": "Arşiv",
    "Garaj Amiri": "Garaj Amiri",
    "Cinayet Büro Amiri": "Cinayet Büro Amiri",
    "Hırsızlık Büro Amiri": "Hırsızlık Büro Amiri",
    "Ahlak Büro Amiri": "Ahlak Büro Amiri",
    "Gasp Büro Amiri": "Gasp Büro Amiri",
    "Tescil Büro Amiri": "Tescil Büro Amiri",
    "Denetleme Büro Amiri": "Denetleme Büro Amiri",
    "Hukuk Müşaviri Avukat": "Huk.Müş.Av.",
    "Avukat": "Avukat",
    "İl Milli Eğitim Müdürü": "İl MEM Md.",
    "İl Sağlık Müdürü": "İl Sağ.Md.",
    "İl Tarım ve Orman Müdürü": "İl Tar.Orm.Md.",
    "İl Müdürü": "İl Md.",
    "Halk Sağlığı Hizmetleri Başkanı": "Halk Sağ.Hizm.Bşk.",
    "Kamu Hastaneleri Hizmetleri Başkanı": "Kamu Hast.Hizm.Bşk.",
    "112 Müdürü": "112 Md.",
    "İl Planlama Müdürü": "İl Plan.Md.",
}

TSV_BIRIM_KISALTMALARI = {
    f"{IL_ADI} Valiliği": f"{IL_ADI} Valiliği",
    f"{IL_ADI} Valiliği Destek Hizmetleri": "Des.Hizm.",
    "Özel Kalem Müdürlüğü": "Özl.Klm.Md.lüğü",
    "Valilik Hukuk İşleri Şube Müdürlüğü": "Huk.İşl.Şb.Md.lüğü",
    "İl Yazı İşleri Müdürlüğü": "İl Yaz.İşl.Md.lüğü",
    "İl Basın ve Halkla İlişkiler Şube Müdürlüğü": "Bsn.Halk.İlş.Şb.Md.lüğü",
    "İl Planlama ve Koordinasyon Müdürlüğü": "Plan.Koor.Md.lüğü",
    "112 Acil Çağrı Merkezi Müdürlüğü": "112 Acil Çağ.Mer.Md.lüğü",
    f"{IL_ADI} İl Jandarma Komutanlığı": "İl J.K.lığı",
    "İl Jandarma Asayiş Şube Müdürlüğü": "Asyş.Şb.Md.lüğü",
    "İl Jandarma Personel Şube Müdürlüğü": "Per.Şb.Md.lüğü",
    "İl Jandarma Lojistik Şube Müdürlüğü": "Loj.Şb.Md.lüğü",
    "İl Jandarma Harekat Şube Müdürlüğü": "Hrk.Şb.Md.lüğü",
    "İl Jandarma Kaçakçılık ve Organize Suçlarla Mücadele Şube Müdürlüğü": "KOM Şb.Md.lüğü",
    "İl Jandarma Siber Suçlarla Mücadele Şube Müdürlüğü": "Sbr.Şb.Md.lüğü",
    "İl Jandarma Trafik Şube Müdürlüğü": "Trf.Şb.Md.lüğü",
    f"{IL_ADI} İl Emniyet Müdürlüğü": "İl Emn.Md.lüğü",
    "İl Emniyet Asayiş Şube Müdürlüğü": "Emn.Asyş.Şb.Md.lüğü",
    "İl Emniyet Trafik Denetleme Şube Müdürlüğü": "Emn.Trf.Şb.Md.lüğü",
    "İl Emniyet Narkotik Suçlarla Mücadele Şube Müdürlüğü": "Emn.Nark.Şb.Md.lüğü",
    "İl Emniyet Terörle Mücadele Şube Müdürlüğü": "Emn.TEM Şb.Md.lüğü",
    "İl Emniyet Siber Suçlarla Mücadele Şube Müdürlüğü": "Emn.Sbr.Şb.Md.lüğü",
    "İl Emniyet Güvenlik Şube Müdürlüğü": "Emn.Güv.Şb.Md.lüğü",
    "İl Emniyet Çevik Kuvvet Şube Müdürlüğü": "Emn.Çev.Kuv.Şb.Md.lüğü",
    "Akçakaya Kaymakamlığı": "Akçakaya Kaym.",
    "Akçakaya Yazı İşleri Müdürlüğü": "Akçakaya Yaz.İşl.",
    "Akçakaya İlçe Nüfus Müdürlüğü": "Akçakaya Nf.Md.",
    "Dereköy Kaymakamlığı": "Dereköy Kaym.",
    "Dereköy Yazı İşleri Müdürlüğü": "Dereköy Yaz.İşl.",
    "Dereköy İlçe Nüfus Müdürlüğü": "Dereköy Nf.Md.",
    "Taşlıca Kaymakamlığı": "Taşlıca Kaym.",
    "Taşlıca Yazı İşleri Müdürlüğü": "Taşlıca Yaz.İşl.",
    "Taşlıca İlçe Nüfus Müdürlüğü": "Taşlıca Nf.Md.",
    "Ilıcapınar Kaymakamlığı": "Ilıcapınar Kaym.",
    "Ilıcapınar Yazı İşleri Müdürlüğü": "Ilıcapınar Yaz.İşl.",
    "Ilıcapınar İlçe Nüfus Müdürlüğü": "Ilıcapınar Nf.Md.",
    f"{IL_ADI} İl Milli Eğitim Müdürlüğü": "İl MEM",
    "İl Milli Eğitim Temel Eğitim Şube Müdürlüğü": "MEM Tem.Eğt.Şb.Md.lüğü",
    "İl Milli Eğitim Ortaöğretim Şube Müdürlüğü": "MEM Ort.Eğt.Şb.Md.lüğü",
    "İl Milli Eğitim Mesleki Eğitim Şube Müdürlüğü": "MEM Mes.Eğt.Şb.Md.lüğü",
    "İl Milli Eğitim İnsan Kaynakları Şube Müdürlüğü": "MEM İK Şb.Md.lüğü",
    "İl Milli Eğitim Destek Hizmetleri Şube Müdürlüğü": "MEM Des.Hizm.Şb.Md.lüğü",
    "İl Milli Eğitim Strateji Geliştirme Şube Müdürlüğü": "MEM Strj.Şb.Md.lüğü",
    f"{IL_ADI} İl Sağlık Müdürlüğü": "İl Sağ.Md.lüğü",
    "İl Sağlık Acil Sağlık Hizmetleri Şube Müdürlüğü": "Sağ.Acil Şb.Md.lüğü",
    "İl Sağlık Bulaşıcı Hastalıklar Şube Müdürlüğü": "Sağ.Bul.Şb.Md.lüğü",
    f"{IL_ADI} İl Tarım ve Orman Müdürlüğü": "İl Tar.Orm.Md.lüğü",
    "İl Tarım Bitkisel Üretim Şube Müdürlüğü": "Tar.Bit.Ürt.Şb.Md.lüğü",
    "İl Tarım Hayvan Sağlığı Şube Müdürlüğü": "Tar.Hay.Sağ.Şb.Md.lüğü",
    "İl Tarım Gıda ve Yem Şube Müdürlüğü": "Tar.Gıda Şb.Md.lüğü",
    f"{IL_ADI} Aile ve Sosyal Hizmetler İl Müdürlüğü": "Aile Sos.Hizm.İl Md.lüğü",
    "Aile Çocuk Hizmetleri Şube Müdürlüğü": "Aile Çck.Şb.Md.lüğü",
    "Aile Engelli ve Yaşlı Hizmetleri Şube Müdürlüğü": "Aile Eng.Şb.Md.lüğü",
}


def build_synthetic_tsv(contacts: list[dict[str, Any]]) -> str:
    """Santral dökümü taklidi TSV metni üretir (access_code, sip_name, department)."""
    lines = ["access_code\tsip_name\tdepartment"]
    for c in contacts:
        if c.get("deleted"):
            continue  # Santral dökümlerinde silinmiş pasif kayıtlar listelenmez
        ext = c["extension"]
        kind = c["kind"]
        if kind == "person":
            sip_name = f"{c['first_name']} {c['last_name']}".strip()
        elif kind == "role":
            sip_name = TSV_UNVAN_KISALTMALARI.get(c["title"], c["title"])
        else:  # department
            sip_name = "Birim hattı"

        dept = TSV_BIRIM_KISALTMALARI.get(c["department"], c["department"])
        lines.append(f"{ext}\t{sip_name}\t{dept}")
    return "\r\n".join(lines) + "\r\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sentetik kurum rehberi üretici")
    parser.add_argument("--cikti", default="data/sentetik_kurum_rehber.json", help="Çıktı JSON dosyası yolu")
    parser.add_argument("--tsv", default="data/sentetik_kurum_rehber.tsv", help="Çıktı TSV dosyası yolu")
    parser.add_argument("--kayit-sayisi", type=int, default=600, help="Üretilecek yaklaşık kayıt sayısı")
    parser.add_argument("--tohum", type=int, default=2026, help="Sabit rastgele tohumu")
    args = parser.parse_args(argv)

    contacts = build_synthetic_directory(target_count=args.kayit_sayisi, seed=args.tohum)

    # JSON Çıktısı
    json_path = Path(args.cikti)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "_aciklama": (
            f"Sentetik kamu kurumu rehberi ({IL_ADI} Valiliği ve bağlı birimleri). "
            "Test ve değerlendirme amaçlı üretilmiştir; gerçek kişi, kurum ve dahili numara içermez."
        ),
        "contacts": contacts,
    }
    json_text = json.dumps(payload, ensure_ascii=False, indent=2)
    json_path.write_text(json_text, encoding="utf-8")

    # TSV Çıktısı (UTF-16 with BOM)
    tsv_path = Path(args.tsv)
    tsv_path.parent.mkdir(parents=True, exist_ok=True)
    tsv_text = build_synthetic_tsv(contacts)
    tsv_path.write_text(tsv_text, encoding="utf-16")

    # Bilgilendirme ve Özet
    sha_json = hashlib.sha256(json_path.read_bytes()).hexdigest()
    sha_tsv = hashlib.sha256(tsv_path.read_bytes()).hexdigest()
    print(f"Başarıyla üretildi: {len(contacts)} kayıt")
    print(f"JSON: {json_path} (SHA-256: {sha_json})")
    print(f"TSV:  {tsv_path} (SHA-256: {sha_tsv})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
