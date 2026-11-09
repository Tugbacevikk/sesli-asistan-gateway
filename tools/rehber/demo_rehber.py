"""Demo Kurum Rehberi Üretici Betiği (Yeşilova Valiliği ve Bağlı Birimleri).

Birim yapılı (kategorili, birim_yolu alanlı) kurum rehberini deterministik olarak
(random.Random(2026)) üretir.

Kullanım:
    python tools/rehber/demo_rehber.py --cikti data/demo_rehber.json
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

UPDATED_AT = "2026-11-09T08:00:00Z"

# Sabit Türkçe ad ve soyad havuzları (deterministik seçim için)
ERKEK_ADLARI = [
    "Ahmet",
    "Ali",
    "Burak",
    "Can",
    "Cem",
    "Emre",
    "Hakan",
    "Hasan",
    "Hüseyin",
    "İbrahim",
    "Kemal",
    "Mehmet",
    "Murat",
    "Mustafa",
    "Okan",
    "Onur",
    "Ömer",
    "Serkan",
    "Tolga",
    "Uğur",
]

KADIN_ADLARI = [
    "Ayşe",
    "Büşra",
    "Ceren",
    "Deniz",
    "Derya",
    "Ebru",
    "Elif",
    "Esra",
    "Fatma",
    "Gamze",
    "Gizem",
    "Hilal",
    "İrem",
    "Merve",
    "Özlem",
    "Seda",
    "Selin",
    "Tuğba",
    "Yasemin",
    "Zeynep",
]

SOYADLARI = [
    "Acar",
    "Arslan",
    "Aydın",
    "Bozkurt",
    "Bulut",
    "Can",
    "Çelik",
    "Demir",
    "Doğan",
    "Erdoğan",
    "Güneş",
    "Kaplan",
    "Kaya",
    "Kılıç",
    "Koç",
    "Kurt",
    "Öztürk",
    "Polat",
    "Şahin",
    "Şen",
    "Yıldırım",
    "Yıldız",
    "Yılmaz",
    "Yurt",
]


class DemoRehberUretici:
    """Yeşilova İli Demo Kurum Rehberi Üreticisi."""

    def __init__(self, seed: int = 2026) -> None:
        self.rng = random.Random(seed)
        self.contacts: list[dict[str, Any]] = []
        self.used_extensions: set[str] = set()
        self.used_ids: set[str] = set()
        # birim adı → o birimin eş anlamlıları (birim hattından); birimdeki diğer hatlar bunları kullanır
        self._birim_alias: dict[str, list[str]] = {}

    def _kayit_ekle(
        self,
        *,
        cid: str,
        kind: str,
        extension: str,
        birim_yolu: list[str],
        kategori: str,
        department: str,
        first_name: str = "",
        last_name: str = "",
        title: str = "",
        aliases: tuple[str, ...] | list[str] = (),
        department_aliases: tuple[str, ...] | list[str] = (),
        transferable: bool = True,
        deleted: bool = False,
    ) -> dict[str, Any]:
        if cid in self.used_ids:
            raise ValueError(f"ID çakışması: {cid}")
        if extension in self.used_extensions:
            raise ValueError(f"Dahili çakışması: {extension}")

        self.used_ids.add(cid)
        self.used_extensions.add(extension)

        # Eş anlamlılar birime aittir: birimdeki her hat birim hattının eş anlamlılarını taşır. Bir hatta kurumun
        # genel adı ("Emniyet") yazılırsa "emniyete bağlar mısınız" o hatta da uyar, birim hattı belirsizleşir.
        if kind == "department":
            if department in self._birim_alias:
                kind = "role"               # birimin ikinci hattı (danışma, yemekhane): birim hattı tek olmalı
            else:
                self._birim_alias[department] = list(department_aliases)
        if kind != "department" and department in self._birim_alias:
            department_aliases = self._birim_alias[department]
        # "operatör" / "santral" asistanda operatör isteğidir; bir hattın takma adı olamaz
        aliases = [a for a in aliases if a.casefold() not in ("operatör", "santral")]

        kayit = {
            "id": cid,
            "kind": kind,
            "first_name": first_name,
            "last_name": last_name,
            "department": department,
            "department_aliases": list(department_aliases),
            "title": title,
            "aliases": list(aliases),
            "extension": str(extension),
            "transferable": transferable,
            "birim_yolu": list(birim_yolu),
            "kategori": kategori,
            "updated_at": UPDATED_AT,
            "deleted": deleted,
        }
        self.contacts.append(kayit)
        return kayit

    def uret(self) -> list[dict[str, Any]]:
        self._uret_valilik()
        self._uret_jandarma()
        self._uret_emniyet()
        self._uret_akcakaya_kaymakamlik()
        self._uret_derekoy_kaymakamlik()
        self._uret_milli_egitim()
        self._uret_silinmis_kayitlar()
        return self.contacts

    # -------------------------------------------------------------------------
    # 1. YEŞİLOVA VALİLİĞİ (prefix: 2xxxx)
    # -------------------------------------------------------------------------
    def _uret_valilik(self) -> None:
        valilik_yol = ["Yeşilova Valiliği"]

        # Kurum Birim Hattı
        self._kayit_ekle(
            cid="v-kurum",
            kind="department",
            extension="20000",
            birim_yolu=valilik_yol,
            kategori="kurum",
            department="Yeşilova Valiliği",
            department_aliases=["Valilik", "Yeşilova Valilik", "Valilik Makamı"],
        )

        # Vali (Aktarılamaz)
        self._kayit_ekle(
            cid="v-vali",
            kind="role",
            extension="20001",
            birim_yolu=valilik_yol,
            kategori="makam",
            department="Yeşilova Valiliği",
            title="Vali",
            aliases=["Yeşilova Valisi", "Vali Bey"],
            department_aliases=["Valilik"],
            transferable=False,
        )

        # Vali Yardımcısı 1 (Kemal Yılmaz)
        self._kayit_ekle(
            cid="v-vali-yrd-1",
            kind="person",
            extension="20002",
            birim_yolu=valilik_yol,
            kategori="makam",
            department="Yeşilova Valiliği",
            first_name="Kemal",
            last_name="Yılmaz",
            title="Vali Yardımcısı",
            aliases=["Vali Yardımcısı Kemal Bey", "Vali Yardımcısı Kemal Yılmaz"],
            department_aliases=["Valilik"],
        )

        # Vali Yardımcısı 2 (Zeynep Kaya)
        self._kayit_ekle(
            cid="v-vali-yrd-2",
            kind="person",
            extension="20003",
            birim_yolu=valilik_yol,
            kategori="makam",
            department="Yeşilova Valiliği",
            first_name="Zeynep",
            last_name="Kaya",
            title="Vali Yardımcısı",
            aliases=["Vali Yardımcısı Zeynep Hanım", "Vali Yardımcısı Zeynep Kaya"],
            department_aliases=["Valilik"],
        )

        # Özel Kalem Müdürlüğü
        kalem_yol = ["Yeşilova Valiliği", "Özel Kalem Müdürlüğü"]
        self._kayit_ekle(
            cid="v-kalem-birim",
            kind="department",
            extension="20010",
            birim_yolu=kalem_yol,
            kategori="kalem",
            department="Valilik Özel Kalem Müdürlüğü",
            department_aliases=["Özel Kalem", "Valilik Özel Kalem"],
        )
        self._kayit_ekle(
            cid="v-kalem-mudur",
            kind="person",
            extension="20011",
            birim_yolu=kalem_yol,
            kategori="makam",
            department="Valilik Özel Kalem Müdürlüğü",
            first_name="Mehmet",
            last_name="Demir",
            title="Özel Kalem Müdürü",
            aliases=["Özel Kalem Mehmet Bey", "Özel Kalem Müdürü Mehmet Demir"],
            department_aliases=["Özel Kalem"],
        )
        self._kayit_ekle(
            cid="v-kalem-gorevli-1",
            kind="person",
            extension="20012",
            birim_yolu=kalem_yol,
            kategori="kişi",
            department="Valilik Özel Kalem Müdürlüğü",
            first_name="Fatma",
            last_name="Çelik",
            title="Sekreter",
            aliases=["Fatma Hanım", "Özel Kalem Fatma Hanım"],
            department_aliases=["Özel Kalem"],
        )
        self._kayit_ekle(
            cid="v-kalem-gorevli-2",
            kind="person",
            extension="20013",
            birim_yolu=kalem_yol,
            kategori="kişi",
            department="Valilik Özel Kalem Müdürlüğü",
            first_name="Can",
            last_name="Arslan",
            title="Protokol Görevlisi",
            aliases=["Can Bey", "Protokol Can Bey"],
            department_aliases=["Özel Kalem"],
        )

        # Hukuk İşleri Şube Müdürlüğü
        hukuk_yol = ["Yeşilova Valiliği", "Hukuk İşleri Şube Müdürlüğü"]
        self._kayit_ekle(
            cid="v-hukuk-birim",
            kind="department",
            extension="20020",
            birim_yolu=hukuk_yol,
            kategori="şube",
            department="Valilik Hukuk İşleri Şube Müdürlüğü",
            department_aliases=["Hukuk İşleri", "Valilik Hukuk", "Hukuk Müşavirliği"],
        )
        self._kayit_ekle(
            cid="v-hukuk-musavir",
            kind="role",
            extension="20021",
            birim_yolu=hukuk_yol,
            kategori="makam",
            department="Valilik Hukuk İşleri Şube Müdürlüğü",
            title="Hukuk Müşaviri",
            aliases=["Valilik Hukuk Müşaviri"],
            department_aliases=["Hukuk İşleri", "Valilik Hukuk"],
        )
        self._kayit_ekle(
            cid="v-hukuk-avukat-1",
            kind="person",
            extension="20022",
            birim_yolu=hukuk_yol,
            kategori="kişi",
            department="Valilik Hukuk İşleri Şube Müdürlüğü",
            first_name="Ali",
            last_name="Şahin",
            title="Avukat",
            aliases=["Avukat Ali Bey", "Avukat Ali Şahin"],
            department_aliases=["Hukuk İşleri"],
        )
        self._kayit_ekle(
            cid="v-hukuk-avukat-2",
            kind="person",
            extension="20023",
            birim_yolu=hukuk_yol,
            kategori="kişi",
            department="Valilik Hukuk İşleri Şube Müdürlüğü",
            first_name="Ebru",
            last_name="Koç",
            title="Avukat",
            aliases=["Avukat Ebru Hanım", "Avukat Ebru Koç"],
            department_aliases=["Hukuk İşleri"],
        )

        # İnsan Kaynakları Şube Müdürlüğü
        ik_yol = ["Yeşilova Valiliği", "İnsan Kaynakları Şube Müdürlüğü"]
        self._kayit_ekle(
            cid="v-ik-birim",
            kind="department",
            extension="20030",
            birim_yolu=ik_yol,
            kategori="şube",
            department="Valilik İnsan Kaynakları Şube Müdürlüğü",
            department_aliases=["İnsan Kaynakları", "Valilik Personel", "Özlük İşleri"],
        )
        self._kayit_ekle(
            cid="v-ik-mudur",
            kind="role",
            extension="20031",
            birim_yolu=ik_yol,
            kategori="makam",
            department="Valilik İnsan Kaynakları Şube Müdürlüğü",
            title="Şube Müdürü",
            aliases=["Valilik İnsan Kaynakları Şube Müdürü", "Valilik İK Müdürü"],
            department_aliases=["İnsan Kaynakları"],
        )
        self._kayit_ekle(
            cid="v-ik-uzman-1",
            kind="person",
            extension="20032",
            birim_yolu=ik_yol,
            kategori="kişi",
            department="Valilik İnsan Kaynakları Şube Müdürlüğü",
            first_name="Hasan",
            last_name="Aydın",
            title="Özlük Uzmanı",
            aliases=["Hasan Bey", "Özlük Hasan Bey"],
            department_aliases=["İnsan Kaynakları"],
        )
        self._kayit_ekle(
            cid="v-ik-uzman-2",
            kind="person",
            extension="20033",
            birim_yolu=ik_yol,
            kategori="kişi",
            department="Valilik İnsan Kaynakları Şube Müdürlüğü",
            first_name="Ayşe",  # Ayşe 1
            last_name="Öztürk",
            title="Personel Şefi",
            aliases=["Ayşe Hanım", "Şef Ayşe Hanım"],
            department_aliases=["İnsan Kaynakları"],
        )
        self._kayit_ekle(
            cid="v-ik-memur-1",
            kind="person",
            extension="20034",
            birim_yolu=ik_yol,
            kategori="kişi",
            department="Valilik İnsan Kaynakları Şube Müdürlüğü",
            first_name="Onur",
            last_name="Güneş",
            title="Memur",
            aliases=["Onur Bey"],
            department_aliases=["İnsan Kaynakları"],
        )

        # Destek Hatları (kategori: destek)
        destek_yol = ["Yeşilova Valiliği", "Destek Hizmetleri"]
        self._kayit_ekle(
            cid="v-destek-birim",
            kind="department",
            extension="20040",
            birim_yolu=destek_yol,
            kategori="destek",
            department="Valilik Destek Hizmetleri",
            title="Destek Hizmetleri",
            department_aliases=["Valilik Destek", "İdari İşler"],
            aliases=["Santral", "Valilik Santral", "Operatör"],
        )
        self._kayit_ekle(
            cid="v-danisma",
            kind="department",
            extension="20041",
            birim_yolu=destek_yol,
            kategori="destek",
            department="Valilik Destek Hizmetleri",
            title="Danışma",
            aliases=["Valilik Danışma", "Giriş Danışma"],
            department_aliases=["Valilik Destek"],
        )
        self._kayit_ekle(
            cid="v-yemekhane",
            kind="department",
            extension="20042",
            birim_yolu=destek_yol,
            kategori="destek",
            department="Valilik Destek Hizmetleri",
            title="Yemekhane",
            aliases=["Valilik Yemekhane", "Yemekhane Sorumlusu"],
            department_aliases=["Valilik Destek"],
        )
        self._kayit_ekle(
            cid="v-bilgi-islem",
            kind="department",
            extension="20043",
            birim_yolu=destek_yol,
            kategori="destek",
            department="Valilik Destek Hizmetleri",
            title="Bilgi İşlem",
            aliases=["Valilik Bilgi İşlem", "Sistem Odası", "Valilik IT"],
            department_aliases=["Valilik Destek"],
        )
        self._kayit_ekle(
            cid="v-nobetci-amiri",
            kind="role",
            extension="20044",
            birim_yolu=destek_yol,
            kategori="destek",
            department="Valilik Destek Hizmetleri",
            title="Nöbetçi Amiri",
            aliases=["Valilik Nöbetçi Amiri"],
            department_aliases=["Valilik Destek"],
        )
        self._kayit_ekle(
            cid="v-belgegecer",
            kind="role",
            extension="20009",
            birim_yolu=destek_yol,
            kategori="destek",
            department="Valilik Destek Hizmetleri",
            title="Belgegeçer",
            aliases=["Valilik Faks", "Valilik Belgegeçer", "Faks Hattı"],
            department_aliases=["Valilik Destek", "Valilik"],
            transferable=False,
        )

    # -------------------------------------------------------------------------
    # 2. YEŞİLOVA İL JANDARMA KOMUTANLIĞI (prefix: 3xxxx)
    # -------------------------------------------------------------------------
    def _uret_jandarma(self) -> None:
        jandarma_yol = ["Yeşilova İl Jandarma Komutanlığı"]

        # Kurum Birim Hattı
        self._kayit_ekle(
            cid="j-kurum",
            kind="department",
            extension="30000",
            birim_yolu=jandarma_yol,
            kategori="kurum",
            department="Yeşilova İl Jandarma Komutanlığı",
            department_aliases=["Jandarma", "İl Jandarma Komutanlığı", "İl Jandarma"],
        )

        # Komutan (Aktarılamaz)
        self._kayit_ekle(
            cid="j-komutan",
            kind="role",
            extension="30001",
            birim_yolu=jandarma_yol,
            kategori="makam",
            department="İl Jandarma Komutanlığı Makamı",
            title="İl Jandarma Komutanı",
            aliases=["Jandarma Komutanı", "Alay Komutanı", "Komutan"],
            department_aliases=["İl Jandarma"],
            transferable=False,
        )

        # Komutan Yardımcısı
        self._kayit_ekle(
            cid="j-komutan-yrd",
            kind="role",
            extension="30002",
            birim_yolu=jandarma_yol,
            kategori="makam",
            department="İl Jandarma Komutanlığı Makamı",
            title="Komutan Yardımcısı",
            aliases=["Jandarma Komutan Yardımcısı", "Alay Komutan Yardımcısı"],
            department_aliases=["İl Jandarma"],
        )

        # Destek Hatları
        destek_yol = ["Yeşilova İl Jandarma Komutanlığı", "Destek Hizmetleri Müdürlüğü"]
        self._kayit_ekle(
            cid="j-destek-birim",
            kind="department",
            extension="30070",
            birim_yolu=destek_yol,
            kategori="destek",
            department="İl Jandarma Destek Hizmetleri Müdürlüğü",
            department_aliases=["Jandarma Destek", "Destek Hizmetleri"],
        )
        self._kayit_ekle(
            cid="j-nobetci-amiri",
            kind="role",
            extension="30080",
            birim_yolu=destek_yol,
            kategori="destek",
            department="İl Jandarma Destek Hizmetleri Müdürlüğü",
            title="Nöbetçi Amiri",
            aliases=["Jandarma Nöbetçi Amiri", "Nöbetçi Subayı"],
            department_aliases=["İl Jandarma", "Jandarma Destek"],
        )
        self._kayit_ekle(
            cid="j-nizamiye",
            kind="role",
            extension="30081",
            birim_yolu=destek_yol,
            kategori="destek",
            department="İl Jandarma Destek Hizmetleri Müdürlüğü",
            title="Nizamiye",
            aliases=["Jandarma Nizamiye", "Ana Giriş Kapısı"],
            department_aliases=["İl Jandarma", "Jandarma Destek"],
        )
        self._kayit_ekle(
            cid="j-belgegecer",
            kind="role",
            extension="30009",
            birim_yolu=destek_yol,
            kategori="destek",
            department="İl Jandarma Destek Hizmetleri Müdürlüğü",
            title="Belgegeçer",
            aliases=["Jandarma Faks", "Jandarma Belgegeçer"],
            department_aliases=["İl Jandarma", "Jandarma Destek"],
            transferable=False,
        )

        # Şubeler (Asayiş, Personel, Lojistik)
        subeler = [
            ("asayis", "Asayiş Şube Müdürlüğü", "30100", "Jandarma Asayiş"),
            ("personel", "Personel Şube Müdürlüğü", "30200", "Jandarma Personel"),
            ("lojistik", "Lojistik Şube Müdürlüğü", "30300", "Jandarma Lojistik"),
        ]

        for s_key, s_ad, base_ext, s_alias in subeler:
            sube_yol = ["Yeşilova İl Jandarma Komutanlığı", s_ad]
            base_num = int(base_ext)

            # Şube Birim Hattı
            self._kayit_ekle(
                cid=f"j-{s_key}-birim",
                kind="department",
                extension=str(base_num),
                birim_yolu=sube_yol,
                kategori="şube",
                department=f"İl Jandarma {s_ad}",
                department_aliases=[s_alias, s_ad],
            )

            # Şube Müdürü (kind=role, kişisiz)
            self._kayit_ekle(
                cid=f"j-{s_key}-mudur",
                kind="role",
                extension=str(base_num + 1),
                birim_yolu=sube_yol,
                kategori="makam",
                department=f"İl Jandarma {s_ad}",
                title=f"{s_ad.replace(' Müdürlüğü', '')} Müdürü" if "Asayiş" in s_ad else "Şube Müdürü",
                aliases=[f"{s_alias} Müdürü", f"Jandarma {s_ad.replace(' Müdürlüğü', '')} Müdürü"],
                department_aliases=[s_alias],
            )

            # İdari İşlemler Astsubayı (kind=role, kişisiz)
            self._kayit_ekle(
                cid=f"j-{s_key}-idari-astsubay",
                kind="role",
                extension=str(base_num + 10),
                birim_yolu=sube_yol,
                kategori="makam",
                department=f"İl Jandarma {s_ad}",
                title="İdari İşlemler Astsubayı",
                aliases=[f"{s_alias} İdari İşlemler"],
                department_aliases=[s_alias],
            )

            # Büro Amiri (kind=role, kişisiz)
            self._kayit_ekle(
                cid=f"j-{s_key}-buro-amiri",
                kind="role",
                extension=str(base_num + 20),
                birim_yolu=sube_yol,
                kategori="makam",
                department=f"İl Jandarma {s_ad}",
                title="Büro Amiri",
                aliases=[f"{s_alias} Büro Amiri"],
                department_aliases=[s_alias],
            )

            # 2 Kişili personel
            p_adlar = [("Uğur", "Çelik"), ("Hüseyin", "Demir")]
            for p_idx, (fn, ln) in enumerate(p_adlar, start=1):
                self._kayit_ekle(
                    cid=f"j-{s_key}-pers-{p_idx}",
                    kind="person",
                    extension=str(base_num + 30 + p_idx),
                    birim_yolu=sube_yol,
                    kategori="kişi",
                    department=f"İl Jandarma {s_ad}",
                    first_name=fn,
                    last_name=ln,
                    title="Astsubay",
                    aliases=[f"{fn} Astsubay", f"{fn} Bey"],
                    department_aliases=[s_alias],
                )

    # -------------------------------------------------------------------------
    # 3. YEŞİLOVA İL EMNİYET MÜDÜRLÜĞÜ (prefix: 4xxxx)
    # -------------------------------------------------------------------------
    def _uret_emniyet(self) -> None:
        emniyet_yol = ["Yeşilova İl Emniyet Müdürlüğü"]

        # Kurum Birim Hattı
        self._kayit_ekle(
            cid="e-kurum",
            kind="department",
            extension="40000",
            birim_yolu=emniyet_yol,
            kategori="kurum",
            department="Yeşilova İl Emniyet Müdürlüğü",
            department_aliases=["Emniyet", "İl Emniyet", "Polis", "Polis Merkezi", "İl Emniyet Müdürlüğü"],
        )

        # İl Emniyet Müdürü
        self._kayit_ekle(
            cid="e-mudur",
            kind="role",
            extension="40001",
            birim_yolu=emniyet_yol,
            kategori="makam",
            department="İl Emniyet Müdürlüğü Makamı",
            title="İl Emniyet Müdürü",
            aliases=["Emniyet Müdürü", "İl Emniyet Müdürü"],
            department_aliases=["Emniyet"],
        )

        # Müdür Yardımcıları
        self._kayit_ekle(
            cid="e-mudur-yrd-1",
            kind="role",
            extension="40002",
            birim_yolu=emniyet_yol,
            kategori="makam",
            department="İl Emniyet Müdürlüğü Makamı",
            title="İl Emniyet Müdür Yardımcısı",
            aliases=["Emniyet Müdür Yardımcısı"],
            department_aliases=["Emniyet"],
        )
        self._kayit_ekle(
            cid="e-mudur-yrd-2",
            kind="role",
            extension="40003",
            birim_yolu=emniyet_yol,
            kategori="makam",
            department="İl Emniyet Müdürlüğü Makamı",
            title="İl Emniyet Müdür Yardımcısı",
            aliases=["Emniyet Müdür Yardımcısı"],
            department_aliases=["Emniyet"],
        )

        # Destek Hatları
        destek_yol = ["Yeşilova İl Emniyet Müdürlüğü", "Destek Hizmetleri Müdürlüğü"]
        self._kayit_ekle(
            cid="e-destek-birim",
            kind="department",
            extension="40070",
            birim_yolu=destek_yol,
            kategori="destek",
            department="İl Emniyet Destek Hizmetleri Müdürlüğü",
            department_aliases=["Emniyet Destek", "Destek Hizmetleri"],
        )
        self._kayit_ekle(
            cid="e-nobetci-amiri",
            kind="role",
            extension="40080",
            birim_yolu=destek_yol,
            kategori="destek",
            department="İl Emniyet Destek Hizmetleri Müdürlüğü",
            title="Nöbetçi Amiri",
            aliases=["Emniyet Nöbetçi Amiri", "Haber Merkezi Amiri"],
            department_aliases=["Emniyet", "Emniyet Destek"],
        )
        self._kayit_ekle(
            cid="e-belgegecer",
            kind="role",
            extension="40009",
            birim_yolu=destek_yol,
            kategori="destek",
            department="İl Emniyet Destek Hizmetleri Müdürlüğü",
            title="Belgegeçer",
            aliases=["Emniyet Faks", "Emniyet Belgegeçer"],
            department_aliases=["Emniyet", "Emniyet Destek"],
            transferable=False,
        )

        # 3.1 Asayiş Şube Müdürlüğü (Altında 3 Büro: Cinayet, Hırsızlık, Gasp)
        asayis_yol = ["Yeşilova İl Emniyet Müdürlüğü", "Asayiş Şube Müdürlüğü"]
        self._kayit_ekle(
            cid="e-asayis-birim",
            kind="department",
            extension="40100",
            birim_yolu=asayis_yol,
            kategori="şube",
            department="İl Emniyet Asayiş Şube Müdürlüğü",
            department_aliases=["Emniyet Asayiş", "Asayiş Şube"],
        )
        self._kayit_ekle(
            cid="e-asayis-mudur",
            kind="role",
            extension="40101",
            birim_yolu=asayis_yol,
            kategori="makam",
            department="İl Emniyet Asayiş Şube Müdürlüğü",
            title="Asayiş Şube Müdürü",
            aliases=["Emniyet Asayiş Şube Müdürü"],
            department_aliases=["Emniyet Asayiş"],
        )

        # Bürolar (Cinayet, Hırsızlık, Gasp)
        burolar = [
            ("cinayet", "Cinayet Bürosu", "40110", ["Cinayet Büro", "Cinayet", "Cinayet Büro Amirliği"]),
            ("hirsizlik", "Hırsızlık Bürosu", "40120", ["Hırsızlık Büro", "Hırsızlık", "Hırsızlık Büro Amirliği"]),
            ("gasp", "Gasp Bürosu", "40130", ["Gasp Büro", "Gasp", "Gasp Büro Amirliği"]),
        ]

        for b_key, b_ad, b_ext, b_aliases in burolar:
            buro_yol = ["Yeşilova İl Emniyet Müdürlüğü", "Asayiş Şube Müdürlüğü", b_ad]
            base_ext_num = int(b_ext)

            # Büro Birim Hattı
            self._kayit_ekle(
                cid=f"e-{b_key}-birim",
                kind="department",
                extension=str(base_ext_num),
                birim_yolu=buro_yol,
                kategori="büro",
                department=f"İl Emniyet {b_ad}",
                department_aliases=b_aliases,
            )

            # Büro Amiri
            self._kayit_ekle(
                cid=f"e-{b_key}-amiri",
                kind="role",
                extension=str(base_ext_num + 1),
                birim_yolu=buro_yol,
                kategori="makam",
                department=f"İl Emniyet {b_ad}",
                title="Büro Amiri",
                aliases=[f"{b_ad.replace(' Bürosu', '')} Büro Amiri", f"{b_ad.replace(' Bürosu', '')} Amiri"],
                department_aliases=b_aliases,
            )

            # Memurlar
            if b_key == "cinayet":
                # Mehmet Kaya 1 (İl Emniyet Cinayet Bürosu)
                self._kayit_ekle(
                    cid="e-cinayet-memur-1",
                    kind="person",
                    extension="40112",
                    birim_yolu=buro_yol,
                    kategori="kişi",
                    department=f"İl Emniyet {b_ad}",
                    first_name="Mehmet",
                    last_name="Kaya",
                    title="Polis Memuru",
                    aliases=["Polis Mehmet Bey", "Mehmet Kaya"],
                    department_aliases=b_aliases,
                )
                self._kayit_ekle(
                    cid="e-cinayet-memur-2",
                    kind="person",
                    extension="40113",
                    birim_yolu=buro_yol,
                    kategori="kişi",
                    department=f"İl Emniyet {b_ad}",
                    first_name="Ahmet",
                    last_name="Yıldız",
                    title="Polis Memuru",
                    aliases=["Polis Ahmet Bey", "Ahmet Bey"],
                    department_aliases=b_aliases,
                )
            elif b_key == "hirsizlik":
                self._kayit_ekle(
                    cid="e-hirsizlik-memur-1",
                    kind="person",
                    extension="40122",
                    birim_yolu=buro_yol,
                    kategori="kişi",
                    department=f"İl Emniyet {b_ad}",
                    first_name="Burak",
                    last_name="Arslan",
                    title="Polis Memuru",
                    aliases=["Polis Burak Bey", "Burak Bey"],
                    department_aliases=b_aliases,
                )
            elif b_key == "gasp":
                self._kayit_ekle(
                    cid="e-gasp-memur-1",
                    kind="person",
                    extension="40132",
                    birim_yolu=buro_yol,
                    kategori="kişi",
                    department=f"İl Emniyet {b_ad}",
                    first_name="Emre",
                    last_name="Kılıç",
                    title="Polis Memuru",
                    aliases=["Polis Emre Bey", "Emre Bey"],
                    department_aliases=b_aliases,
                )

        # 3.2 Trafik Şube Müdürlüğü
        trafik_yol = ["Yeşilova İl Emniyet Müdürlüğü", "Trafik Şube Müdürlüğü"]
        self._kayit_ekle(
            cid="e-trafik-birim",
            kind="department",
            extension="40200",
            birim_yolu=trafik_yol,
            kategori="şube",
            department="İl Emniyet Trafik Şube Müdürlüğü",
            department_aliases=["Emniyet Trafik", "Trafik Şube", "Trafik Denetleme"],
        )
        self._kayit_ekle(
            cid="e-trafik-mudur",
            kind="role",
            extension="40201",
            birim_yolu=trafik_yol,
            kategori="makam",
            department="İl Emniyet Trafik Şube Müdürlüğü",
            title="Trafik Şube Müdürü",
            aliases=["Emniyet Trafik Şube Müdürü"],
            department_aliases=["Emniyet Trafik"],
        )
        self._kayit_ekle(
            cid="e-trafik-buro-amiri",
            kind="role",
            extension="40210",
            birim_yolu=trafik_yol,
            kategori="makam",
            department="İl Emniyet Trafik Şube Müdürlüğü",
            title="Büro Amiri",
            aliases=["Trafik Büro Amiri", "Trafik Tescil Amiri"],
            department_aliases=["Emniyet Trafik"],
        )
        # Ayşe 2 (Ayşe Demir, Trafik)
        self._kayit_ekle(
            cid="e-trafik-memur-1",
            kind="person",
            extension="40212",
            birim_yolu=trafik_yol,
            kategori="kişi",
            department="İl Emniyet Trafik Şube Müdürlüğü",
            first_name="Ayşe",
            last_name="Demir",
            title="Polis Memuru",
            aliases=["Ayşe Hanım", "Polis Ayşe Hanım"],
            department_aliases=["Emniyet Trafik"],
        )
        self._kayit_ekle(
            cid="e-trafik-memur-2",
            kind="person",
            extension="40213",
            birim_yolu=trafik_yol,
            kategori="kişi",
            department="İl Emniyet Trafik Şube Müdürlüğü",
            first_name="Murat",
            last_name="Şen",
            title="Polis Memuru",
            aliases=["Murat Bey", "Trafikçi Murat Bey"],
            department_aliases=["Emniyet Trafik"],
        )

        # 3.3 Narkotik Şube Müdürlüğü
        narkotik_yol = ["Yeşilova İl Emniyet Müdürlüğü", "Narkotik Şube Müdürlüğü"]
        self._kayit_ekle(
            cid="e-narkotik-birim",
            kind="department",
            extension="40300",
            birim_yolu=narkotik_yol,
            kategori="şube",
            department="İl Emniyet Narkotik Şube Müdürlüğü",
            department_aliases=["Emniyet Narkotik", "Narkotik Şube", "Narkotik"],
        )
        self._kayit_ekle(
            cid="e-narkotik-mudur",
            kind="role",
            extension="40301",
            birim_yolu=narkotik_yol,
            kategori="makam",
            department="İl Emniyet Narkotik Şube Müdürlüğü",
            title="Narkotik Şube Müdürü",
            aliases=["Emniyet Narkotik Şube Müdürü"],
            department_aliases=["Emniyet Narkotik"],
        )
        self._kayit_ekle(
            cid="e-narkotik-buro-amiri",
            kind="role",
            extension="40310",
            birim_yolu=narkotik_yol,
            kategori="makam",
            department="İl Emniyet Narkotik Şube Müdürlüğü",
            title="Büro Amiri",
            aliases=["Narkotik Büro Amiri"],
            department_aliases=["Emniyet Narkotik"],
        )
        self._kayit_ekle(
            cid="e-narkotik-memur-1",
            kind="person",
            extension="40312",
            birim_yolu=narkotik_yol,
            kategori="kişi",
            department="İl Emniyet Narkotik Şube Müdürlüğü",
            first_name="Serkan",
            last_name="Kurt",
            title="Polis Memuru",
            aliases=["Serkan Bey"],
            department_aliases=["Emniyet Narkotik"],
        )

    # -------------------------------------------------------------------------
    # 4. AKÇAKAYA KAYMAKAMLIĞI (prefix: 51xxx)
    # -------------------------------------------------------------------------
    def _uret_akcakaya_kaymakamlik(self) -> None:
        akcakaya_yol = ["Akçakaya Kaymakamlığı"]

        # Kurum Birim Hattı
        self._kayit_ekle(
            cid="k-akcakaya-kurum",
            kind="department",
            extension="51000",
            birim_yolu=akcakaya_yol,
            kategori="kurum",
            department="Akçakaya Kaymakamlığı",
            department_aliases=["Akçakaya Kaymakamlık", "Akçakaya İlçe", "Akçakaya"],
        )

        # Kaymakam (Kişili makam: Serkan Doğan)
        self._kayit_ekle(
            cid="k-akcakaya-kaymakam",
            kind="person",
            extension="51001",
            birim_yolu=akcakaya_yol,
            kategori="makam",
            department="Akçakaya Kaymakamlığı",
            first_name="Serkan",
            last_name="Doğan",
            title="Kaymakam",
            aliases=["Akçakaya Kaymakamı", "Serkan Doğan", "Kaymakam Serkan Bey", "Serkan Bey"],
            department_aliases=["Akçakaya Kaymakamlığı"],
        )

        # Yazı İşleri Müdürlüğü
        yazi_yol = ["Akçakaya Kaymakamlığı", "Yazı İşleri Müdürlüğü"]
        self._kayit_ekle(
            cid="k-akcakaya-yazi-birim",
            kind="department",
            extension="51010",
            birim_yolu=yazi_yol,
            kategori="şube",
            department="Akçakaya Yazı İşleri Müdürlüğü",
            department_aliases=["Akçakaya Yazı İşleri", "Yazı İşleri"],
        )
        self._kayit_ekle(
            cid="k-akcakaya-yazi-mudur",
            kind="person",
            extension="51011",
            birim_yolu=yazi_yol,
            kategori="makam",
            department="Akçakaya Yazı İşleri Müdürlüğü",
            first_name="Mustafa",
            last_name="Yılmaz",
            title="Yazı İşleri Müdürü",
            aliases=["Mustafa Bey", "Yazı İşleri Müdürü Mustafa Bey"],
            department_aliases=["Akçakaya Yazı İşleri"],
        )
        self._kayit_ekle(
            cid="k-akcakaya-yazi-memur-1",
            kind="person",
            extension="51012",
            birim_yolu=yazi_yol,
            kategori="kişi",
            department="Akçakaya Yazı İşleri Müdürlüğü",
            first_name="Gizem",
            last_name="Polat",
            title="VHKİ",
            aliases=["Gizem Hanım"],
            department_aliases=["Akçakaya Yazı İşleri"],
        )

        # İlçe Nüfus Müdürlüğü
        nufus_yol = ["Akçakaya Kaymakamlığı", "İlçe Nüfus Müdürlüğü"]
        self._kayit_ekle(
            cid="k-akcakaya-nufus-birim",
            kind="department",
            extension="51020",
            birim_yolu=nufus_yol,
            kategori="şube",
            department="Akçakaya İlçe Nüfus Müdürlüğü",
            department_aliases=["Akçakaya Nüfus", "Akçakaya Nüfus Müdürlüğü", "Nüfus"],
        )
        self._kayit_ekle(
            cid="k-akcakaya-nufus-mudur",
            kind="person",
            extension="51021",
            birim_yolu=nufus_yol,
            kategori="makam",
            department="Akçakaya İlçe Nüfus Müdürlüğü",
            first_name="Elif",
            last_name="Kurt",
            title="Nüfus Müdürü",
            aliases=["Elif Hanım", "Nüfus Müdürü Elif Hanım"],
            department_aliases=["Akçakaya Nüfus"],
        )
        self._kayit_ekle(
            cid="k-akcakaya-nufus-memur-1",
            kind="person",
            extension="51022",
            birim_yolu=nufus_yol,
            kategori="kişi",
            department="Akçakaya İlçe Nüfus Müdürlüğü",
            first_name="Derya",
            last_name="Bulut",
            title="Nüfus Memuru",
            aliases=["Derya Hanım"],
            department_aliases=["Akçakaya Nüfus"],
        )

        # Belgegeçer (Aktarılamaz)
        self._kayit_ekle(
            cid="k-akcakaya-belgegecer",
            kind="role",
            extension="51009",
            birim_yolu=akcakaya_yol,
            kategori="destek",
            department="Akçakaya Kaymakamlığı",
            title="Belgegeçer",
            aliases=["Akçakaya Faks", "Akçakaya Belgegeçer"],
            department_aliases=["Akçakaya Kaymakamlığı"],
            transferable=False,
        )

    # -------------------------------------------------------------------------
    # 5. DEREKÖY KAYMAKAMLIĞI (prefix: 52xxx)
    # -------------------------------------------------------------------------
    def _uret_derekoy_kaymakamlik(self) -> None:
        derekoy_yol = ["Dereköy Kaymakamlığı"]

        # Kurum Birim Hattı
        self._kayit_ekle(
            cid="k-derekoy-kurum",
            kind="department",
            extension="52000",
            birim_yolu=derekoy_yol,
            kategori="kurum",
            department="Dereköy Kaymakamlığı",
            department_aliases=["Dereköy Kaymakamlık", "Dereköy İlçe", "Dereköy"],
        )

        # Kaymakam (Kişili makam: Okan Yıldırım)
        self._kayit_ekle(
            cid="k-derekoy-kaymakam",
            kind="person",
            extension="52001",
            birim_yolu=derekoy_yol,
            kategori="makam",
            department="Dereköy Kaymakamlığı",
            first_name="Okan",
            last_name="Yıldırım",
            title="Kaymakam",
            aliases=["Dereköy Kaymakamı", "Okan Yıldırım", "Kaymakam Okan Bey", "Okan Bey"],
            department_aliases=["Dereköy Kaymakamlığı"],
        )

        # Yazı İşleri Müdürlüğü
        yazi_yol = ["Dereköy Kaymakamlığı", "Yazı İşleri Müdürlüğü"]
        self._kayit_ekle(
            cid="k-derekoy-yazi-birim",
            kind="department",
            extension="52010",
            birim_yolu=yazi_yol,
            kategori="şube",
            department="Dereköy Yazı İşleri Müdürlüğü",
            department_aliases=["Dereköy Yazı İşleri", "Yazı İşleri"],
        )
        self._kayit_ekle(
            cid="k-derekoy-yazi-mudur",
            kind="person",
            extension="52011",
            birim_yolu=yazi_yol,
            kategori="makam",
            department="Dereköy Yazı İşleri Müdürlüğü",
            first_name="Murat",
            last_name="Şahin",
            title="Yazı İşleri Müdürü",
            aliases=["Murat Bey", "Yazı İşleri Müdürü Murat Bey"],
            department_aliases=["Dereköy Yazı İşleri"],
        )
        self._kayit_ekle(
            cid="k-derekoy-yazi-memur-1",
            kind="person",
            extension="52012",
            birim_yolu=yazi_yol,
            kategori="kişi",
            department="Dereköy Yazı İşleri Müdürlüğü",
            first_name="Hilal",
            last_name="Aydın",
            title="VHKİ",
            aliases=["Hilal Hanım"],
            department_aliases=["Dereköy Yazı İşleri"],
        )

        # İlçe Nüfus Müdürlüğü
        nufus_yol = ["Dereköy Kaymakamlığı", "İlçe Nüfus Müdürlüğü"]
        self._kayit_ekle(
            cid="k-derekoy-nufus-birim",
            kind="department",
            extension="52020",
            birim_yolu=nufus_yol,
            kategori="şube",
            department="Dereköy İlçe Nüfus Müdürlüğü",
            department_aliases=["Dereköy Nüfus", "Dereköy Nüfus Müdürlüğü", "Nüfus"],
        )
        self._kayit_ekle(
            cid="k-derekoy-nufus-mudur",
            kind="person",
            extension="52021",
            birim_yolu=nufus_yol,
            kategori="makam",
            department="Dereköy İlçe Nüfus Müdürlüğü",
            first_name="Deniz",
            last_name="Polat",
            title="Nüfus Müdürü",
            aliases=["Deniz Hanım", "Nüfus Müdürü Deniz Hanım"],
            department_aliases=["Dereköy Nüfus"],
        )
        self._kayit_ekle(
            cid="k-derekoy-nufus-memur-1",
            kind="person",
            extension="52022",
            birim_yolu=nufus_yol,
            kategori="kişi",
            department="Dereköy İlçe Nüfus Müdürlüğü",
            first_name="Tolga",
            last_name="Can",
            title="Nüfus Memuru",
            aliases=["Tolga Bey"],
            department_aliases=["Dereköy Nüfus"],
        )

        # Belgegeçer (Aktarılamaz)
        self._kayit_ekle(
            cid="k-derekoy-belgegecer",
            kind="role",
            extension="52009",
            birim_yolu=derekoy_yol,
            kategori="destek",
            department="Dereköy Kaymakamlığı",
            title="Belgegeçer",
            aliases=["Dereköy Faks", "Dereköy Belgegeçer"],
            department_aliases=["Dereköy Kaymakamlığı"],
            transferable=False,
        )

    # -------------------------------------------------------------------------
    # 6. YEŞİLOVA İL MİLLİ EĞİTİM MÜDÜRLÜĞÜ (prefix: 6xxxx)
    # -------------------------------------------------------------------------
    def _uret_milli_egitim(self) -> None:
        meb_yol = ["Yeşilova İl Milli Eğitim Müdürlüğü"]

        # Kurum Birim Hattı
        self._kayit_ekle(
            cid="m-kurum",
            kind="department",
            extension="60000",
            birim_yolu=meb_yol,
            kategori="kurum",
            department="Yeşilova İl Milli Eğitim Müdürlüğü",
            department_aliases=[
                "Milli Eğitim",
                "İl Milli Eğitim",
                "Milli Eğitim Müdürlüğü",
                "İl Milli Eğitim Müdürlüğü",
                "MEB",
            ],
        )

        # İl Müdürü
        self._kayit_ekle(
            cid="m-mudur",
            kind="role",
            extension="60001",
            birim_yolu=meb_yol,
            kategori="makam",
            department="İl Milli Eğitim Müdürlüğü Makamı",
            title="İl Milli Eğitim Müdürü",
            aliases=["Milli Eğitim Müdürü", "İl Müdürü"],
            department_aliases=["İl Milli Eğitim"],
        )

        # Belgegeçer
        self._kayit_ekle(
            cid="m-belgegecer",
            kind="role",
            extension="60009",
            birim_yolu=meb_yol,
            kategori="destek",
            department="Yeşilova İl Milli Eğitim Müdürlüğü",
            title="Belgegeçer",
            aliases=["Milli Eğitim Faks", "MEB Faks", "Milli Eğitim Belgegeçer"],
            department_aliases=["İl Milli Eğitim"],
            transferable=False,
        )

        # 6.1 Ortaöğretim Şube Müdürlüğü
        orta_yol = ["Yeşilova İl Milli Eğitim Müdürlüğü", "Ortaöğretim Şube Müdürlüğü"]
        self._kayit_ekle(
            cid="m-orta-birim",
            kind="department",
            extension="60100",
            birim_yolu=orta_yol,
            kategori="şube",
            department="İl Milli Eğitim Ortaöğretim Şube Müdürlüğü",
            department_aliases=["Ortaöğretim Şube", "Ortaöğretim", "Liseler Şubesi"],
        )
        self._kayit_ekle(
            cid="m-orta-mudur",
            kind="role",
            extension="60101",
            birim_yolu=orta_yol,
            kategori="makam",
            department="İl Milli Eğitim Ortaöğretim Şube Müdürlüğü",
            title="Şube Müdürü",
            aliases=["Ortaöğretim Şube Müdürü", "MEB Ortaöğretim Şube Müdürü"],
            department_aliases=["Ortaöğretim Şube"],
        )
        # Mehmet Kaya 2 (İl Milli Eğitim Ortaöğretim Şube Müdürlüğü)
        self._kayit_ekle(
            cid="m-orta-uzman-1",
            kind="person",
            extension="60102",
            birim_yolu=orta_yol,
            kategori="kişi",
            department="İl Milli Eğitim Ortaöğretim Şube Müdürlüğü",
            first_name="Mehmet",
            last_name="Kaya",
            title="Eğitim Uzmanı",
            aliases=["Mehmet Bey", "Eğitim Uzmanı Mehmet Bey", "Mehmet Kaya"],
            department_aliases=["Ortaöğretim Şube"],
        )
        # Ayşe 3 (Ayşe Acar, Ortaöğretim)
        self._kayit_ekle(
            cid="m-orta-uzman-2",
            kind="person",
            extension="60103",
            birim_yolu=orta_yol,
            kategori="kişi",
            department="İl Milli Eğitim Ortaöğretim Şube Müdürlüğü",
            first_name="Ayşe",
            last_name="Acar",
            title="Öğretmen",
            aliases=["Ayşe Hanım", "Öğretmen Ayşe Hanım"],
            department_aliases=["Ortaöğretim Şube"],
        )
        self._kayit_ekle(
            cid="m-orta-memur-1",
            kind="person",
            extension="60104",
            birim_yolu=orta_yol,
            kategori="kişi",
            department="İl Milli Eğitim Ortaöğretim Şube Müdürlüğü",
            first_name="Cem",
            last_name="Öztürk",
            title="Memur",
            aliases=["Cem Bey"],
            department_aliases=["Ortaöğretim Şube"],
        )

        # 6.2 Temel Eğitim Şube Müdürlüğü
        temel_yol = ["Yeşilova İl Milli Eğitim Müdürlüğü", "Temel Eğitim Şube Müdürlüğü"]
        self._kayit_ekle(
            cid="m-temel-birim",
            kind="department",
            extension="60200",
            birim_yolu=temel_yol,
            kategori="şube",
            department="İl Milli Eğitim Temel Eğitim Şube Müdürlüğü",
            department_aliases=["Temel Eğitim Şube", "Temel Eğitim", "İlkokullar Şubesi"],
        )
        self._kayit_ekle(
            cid="m-temel-mudur",
            kind="role",
            extension="60201",
            birim_yolu=temel_yol,
            kategori="makam",
            department="İl Milli Eğitim Temel Eğitim Şube Müdürlüğü",
            title="Şube Müdürü",
            aliases=["Temel Eğitim Şube Müdürü", "MEB Temel Eğitim Şube Müdürü"],
            department_aliases=["Temel Eğitim Şube"],
        )
        self._kayit_ekle(
            cid="m-temel-uzman-1",
            kind="person",
            extension="60202",
            birim_yolu=temel_yol,
            kategori="kişi",
            department="İl Milli Eğitim Temel Eğitim Şube Müdürlüğü",
            first_name="Cemil",
            last_name="Erdoğan",
            title="Eğitim Uzmanı",
            aliases=["Cemil Bey", "Eğitim Uzmanı Cemil Bey"],
            department_aliases=["Temel Eğitim Şube"],
        )
        self._kayit_ekle(
            cid="m-temel-uzman-2",
            kind="person",
            extension="60203",
            birim_yolu=temel_yol,
            kategori="kişi",
            department="İl Milli Eğitim Temel Eğitim Şube Müdürlüğü",
            first_name="Selin",
            last_name="Yurt",
            title="Eğitim Uzmanı",
            aliases=["Selin Hanım"],
            department_aliases=["Temel Eğitim Şube"],
        )
        self._kayit_ekle(
            cid="m-temel-memur-1",
            kind="person",
            extension="60204",
            birim_yolu=temel_yol,
            kategori="kişi",
            department="İl Milli Eğitim Temel Eğitim Şube Müdürlüğü",
            first_name="İbrahim",
            last_name="Bozkurt",
            title="Memur",
            aliases=["İbrahim Bey"],
            department_aliases=["Temel Eğitim Şube"],
        )

        # Ekstra kadro (toplam sayıyı 120-160 bandına getirmek için birimlere dengeli memur/uzman eklenir)
        self._ekstra_kadro_doldur()

    def _ekstra_kadro_doldur(self) -> None:
        """Kayıt sayısını 120-160 aralığına dengeli biçimde tamamlar."""
        # Ek kadrolar eklenecek birim yolları ve adları
        kadrolar = [
            (
                "Yeşilova Valiliği",
                ["Yeşilova Valiliği", "Hukuk İşleri Şube Müdürlüğü"],
                "Valilik Hukuk İşleri Şube Müdürlüğü",
                "şube",
                "2002",
                "Hukuk",
            ),
            (
                "Yeşilova Valiliği",
                ["Yeşilova Valiliği", "İnsan Kaynakları Şube Müdürlüğü"],
                "Valilik İnsan Kaynakları Şube Müdürlüğü",
                "şube",
                "2003",
                "İK",
            ),
            (
                "Yeşilova İl Jandarma Komutanlığı",
                ["Yeşilova İl Jandarma Komutanlığı", "Asayiş Şube Müdürlüğü"],
                "İl Jandarma Asayiş Şube Müdürlüğü",
                "şube",
                "3014",
                "Jandarma Asayiş",
            ),
            (
                "Yeşilova İl Jandarma Komutanlığı",
                ["Yeşilova İl Jandarma Komutanlığı", "Personel Şube Müdürlüğü"],
                "İl Jandarma Personel Şube Müdürlüğü",
                "şube",
                "3024",
                "Jandarma Personel",
            ),
            (
                "Yeşilova İl Jandarma Komutanlığı",
                ["Yeşilova İl Jandarma Komutanlığı", "Lojistik Şube Müdürlüğü"],
                "İl Jandarma Lojistik Şube Müdürlüğü",
                "şube",
                "3034",
                "Jandarma Lojistik",
            ),
            (
                "Yeşilova İl Emniyet Müdürlüğü",
                ["Yeşilova İl Emniyet Müdürlüğü", "Trafik Şube Müdürlüğü"],
                "İl Emniyet Trafik Şube Müdürlüğü",
                "şube",
                "4022",
                "Trafik",
            ),
            (
                "Yeşilova İl Emniyet Müdürlüğü",
                ["Yeşilova İl Emniyet Müdürlüğü", "Narkotik Şube Müdürlüğü"],
                "İl Emniyet Narkotik Şube Müdürlüğü",
                "şube",
                "4032",
                "Narkotik",
            ),
            (
                "Akçakaya Kaymakamlığı",
                ["Akçakaya Kaymakamlığı", "Yazı İşleri Müdürlüğü"],
                "Akçakaya Yazı İşleri Müdürlüğü",
                "şube",
                "5103",
                "Yazı İşleri",
            ),
            (
                "Akçakaya Kaymakamlığı",
                ["Akçakaya Kaymakamlığı", "İlçe Nüfus Müdürlüğü"],
                "Akçakaya İlçe Nüfus Müdürlüğü",
                "şube",
                "5104",
                "Nüfus",
            ),
            (
                "Dereköy Kaymakamlığı",
                ["Dereköy Kaymakamlığı", "Yazı İşleri Müdürlüğü"],
                "Dereköy Yazı İşleri Müdürlüğü",
                "şube",
                "5203",
                "Yazı İşleri",
            ),
            (
                "Dereköy Kaymakamlığı",
                ["Dereköy Kaymakamlığı", "İlçe Nüfus Müdürlüğü"],
                "Dereköy İlçe Nüfus Müdürlüğü",
                "şube",
                "5204",
                "Nüfus",
            ),
            (
                "Yeşilova İl Milli Eğitim Müdürlüğü",
                ["Yeşilova İl Milli Eğitim Müdürlüğü", "Ortaöğretim Şube Müdürlüğü"],
                "İl Milli Eğitim Ortaöğretim Şube Müdürlüğü",
                "şube",
                "6011",
                "MEB",
            ),
            (
                "Yeşilova İl Milli Eğitim Müdürlüğü",
                ["Yeşilova İl Milli Eğitim Müdürlüğü", "Temel Eğitim Şube Müdürlüğü"],
                "İl Milli Eğitim Temel Eğitim Şube Müdürlüğü",
                "şube",
                "6021",
                "MEB",
            ),
        ]

        ekstra_unvanlar = ["Uzman", "VHKİ", "Memur", "Tekniker", "Şef"]

        idx = 0
        while len(self.contacts) < 137:  # 137 + 3 silinmiş = 140 toplam kayıt
            target_birim = kadrolar[idx % len(kadrolar)]
            kurum_ad, yol, dept_ad, kat, ext_prefix, alias_prefix = target_birim
            idx += 1

            # Cinsiyet ve ad seçimi
            is_kadin = self.rng.random() < 0.5
            ad_list = KADIN_ADLARI if is_kadin else ERKEK_ADLARI
            fn = self.rng.choice(ad_list)
            # Bilinçli çakışma olan ad-soyadları rastgele havuza sokmayalım
            if fn in ("Ayşe", "Mehmet"):
                fn = "Derya" if is_kadin else "Hakan"
            ln = self.rng.choice(SOYADLARI)
            if fn == "Mehmet" and ln == "Kaya":
                ln = "Demir"

            unvan = self.rng.choice(ekstra_unvanlar)
            ext_suffix = f"{idx:02d}"
            ext = f"{ext_prefix[:3]}{ext_suffix}"
            if ext in self.used_extensions:
                continue

            cid = f"ekstra-{idx}-{fn.lower()}-{ln.lower()}"
            self._kayit_ekle(
                cid=cid,
                kind="person",
                extension=ext,
                birim_yolu=yol,
                kategori="kişi",
                department=dept_ad,
                first_name=fn,
                last_name=ln,
                title=unvan,
                aliases=[f"{fn} {ln}", f"{fn} Bey" if not is_kadin else f"{fn} Hanım"],
                department_aliases=[alias_prefix],
            )

    # -------------------------------------------------------------------------
    # 7. SİLİNMİŞ KAYITLAR (deleted=True, tam 3 adet)
    # -------------------------------------------------------------------------
    def _uret_silinmis_kayitlar(self) -> None:
        self._kayit_ekle(
            cid="del-valilik-eski-personel",
            kind="person",
            extension="20099",
            birim_yolu=["Yeşilova Valiliği", "İnsan Kaynakları Şube Müdürlüğü"],
            kategori="kişi",
            department="Valilik İnsan Kaynakları Şube Müdürlüğü",
            first_name="Bülent",
            last_name="Eski",
            title="Eski Uzman",
            aliases=["Bülent Bey"],
            department_aliases=["İnsan Kaynakları"],
            deleted=True,
        )
        self._kayit_ekle(
            cid="del-jandarma-eski-astsubay",
            kind="person",
            extension="30099",
            birim_yolu=["Yeşilova İl Jandarma Komutanlığı", "Asayiş Şube Müdürlüğü"],
            kategori="kişi",
            department="İl Jandarma Asayiş Şube Müdürlüğü",
            first_name="Kemal",
            last_name="Ayrılan",
            title="Eski Astsubay",
            aliases=["Kemal Bey"],
            department_aliases=["Jandarma Asayiş"],
            deleted=True,
        )
        self._kayit_ekle(
            cid="del-emniyet-eski-memur",
            kind="person",
            extension="40099",
            birim_yolu=["Yeşilova İl Emniyet Müdürlüğü", "Trafik Şube Müdürlüğü"],
            kategori="kişi",
            department="İl Emniyet Trafik Şube Müdürlüğü",
            first_name="Zafer",
            last_name="Geçmiş",
            title="Eski Polis Memuru",
            aliases=["Zafer Bey"],
            department_aliases=["Emniyet Trafik"],
            deleted=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Demo kurum rehberi üretici betiği.")
    parser.add_argument("--cikti", default="data/demo_rehber.json", help="Çıktı JSON dosyası yolu")
    args = parser.parse_args()

    uretici = DemoRehberUretici(seed=2026)
    contacts = uretici.uret()

    cikti_yolu = Path(args.cikti)
    cikti_yolu.parent.mkdir(parents=True, exist_ok=True)

    rehber_verisi = {
        "_aciklama": "Sentetik kurum rehberi (demo seti, birim_yolu ve kategori alanlı).",
        "contacts": contacts,
    }

    with open(cikti_yolu, "w", encoding="utf-8") as f:
        json.dump(rehber_verisi, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"Demo rehberi üretildi: {len(contacts)} kayıt -> {cikti_yolu}")


if __name__ == "__main__":
    main()
