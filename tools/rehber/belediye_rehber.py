"""Görülmemiş kurum test rehberi: uydurma bir belediye (Ilgınova Belediyesi).

Anlama modelinin YENİ bir kurumun rehberine genellenmesini ölçmek için (v9, 26.11.2026). Eğitim verisinde hiç
kullanılmaz (`tools/egitim/anlama_veri.py` test rehberi). Demo rehberinden (valilik / jandarma / emniyet, Yeşilova)
bilerek farklıdır: başka il ve ilçe adları, belediye müdürlükleri, kısaltmalı bağlı kuruluş (ILSU), itfaiye
rütbeleri, hizmet hatları (çağrı merkezi, zabıta nöbetçi, cenaze), aktarılamayan başkanlık makamı.
Ad ve soyadlar demo rehberinin havuzundan (gerçek kişi yok).

    python tools/rehber/belediye_rehber.py              # data/belediye_rehber.json
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/rehber"))
from demo_rehber import ERKEK_ADLARI, KADIN_ADLARI, SOYADLARI  # noqa: E402

UPDATED_AT = "2026-11-26T08:00:00Z"
KURUM = "Ilgınova Belediyesi"

Kadro = list[tuple[str, bool, bool, list[str]]]


def P(unvan: str, *takma: str) -> tuple[str, bool, bool, list[str]]:      # kişi
    return (unvan, True, True, list(takma))


def R(unvan: str, *takma: str) -> tuple[str, bool, bool, list[str]]:      # kişisiz hat (makam / hizmet)
    return (unvan, False, True, list(takma))


# (anahtar, birim adı, eş adlar, dahili başı, kadro)
MUDURLUKLER: list[tuple[str, str, list[str], int, Kadro]] = [
    ("fen", "Fen İşleri Müdürlüğü", ["Fen İşleri", "Yol Yapım"], 310,
     [P("Fen İşleri Müdürü"), P("İnşaat Mühendisi"), P("Harita Teknikeri"), P("Asfalt Şantiye Şefi", "Asfalt Şefi")]),
    ("imar", "İmar ve Şehircilik Müdürlüğü", ["İmar", "İmar Müdürlüğü", "Ruhsat"], 320,
     [P("İmar Müdürü"), P("Yapı Ruhsat Şefi", "Ruhsat Şefi"), P("Mimar"), P("Yapı Denetim Memuru")]),
    ("zabita", "Zabıta Müdürlüğü", ["Zabıta", "Belediye Zabıtası"], 330,
     [P("Zabıta Müdürü"), P("Zabıta Amiri", "Zabıta Komiseri"), P("Zabıta Memuru"),
      R("Zabıta Nöbetçi Amirliği", "Nöbetçi Zabıta")]),
    ("temizlik", "Temizlik İşleri Müdürlüğü", ["Temizlik İşleri", "Çöp", "Çevre Temizlik"], 340,
     [P("Temizlik İşleri Müdürü"), P("Saha Sorumlusu"), P("Çöp Toplama Şefi")]),
    ("park", "Park ve Bahçeler Müdürlüğü", ["Park Bahçeler", "Yeşil Alan"], 350,
     [P("Park ve Bahçeler Müdürü"), P("Ziraat Mühendisi"), P("Bahçıvan Ustabaşı")]),
    ("mali", "Mali Hizmetler Müdürlüğü", ["Mali Hizmetler", "Muhasebe", "Tahakkuk"], 360,
     [P("Mali Hizmetler Müdürü"), P("Muhasebe Şefi"), P("Emlak Vergisi Memuru", "Emlak Servisi"), P("Tahsildar")]),
    ("ik", "İnsan Kaynakları ve Eğitim Müdürlüğü", ["İnsan Kaynakları", "Personel", "Özlük"], 370,
     [P("İnsan Kaynakları Müdürü"), P("Özlük İşleri Şefi", "Personel Şefi"), P("Bordro Memuru")]),
    ("yazi", "Yazı İşleri Müdürlüğü", ["Yazı İşleri", "Evrak", "Meclis"], 380,
     [P("Yazı İşleri Müdürü"), P("Meclis Kalemi", "Meclis Kalem"), P("Evrak Kayıt Memuru")]),
    ("hukuk", "Hukuk İşleri Müdürlüğü", ["Hukuk İşleri", "Avukatlık"], 390,
     [P("Hukuk İşleri Müdürü"), P("Avukat", "Belediye Avukatı")]),
    ("bilgi", "Bilgi İşlem Müdürlüğü", ["Bilgi İşlem", "Bilişim"], 400,
     [P("Bilgi İşlem Müdürü"), P("Sistem Yöneticisi"), P("Yazılım Uzmanı")]),
    ("kultur", "Kültür ve Sosyal İşler Müdürlüğü", ["Kültür İşleri", "Sosyal İşler", "Kültür Merkezi"], 410,
     [P("Kültür ve Sosyal İşler Müdürü"), P("Kurs Koordinatörü"), P("Sosyal Yardım Memuru", "Sosyal Yardım")]),
    ("veteriner", "Veteriner İşleri Müdürlüğü", ["Veteriner", "Sokak Hayvanları"], 420,
     [P("Veteriner Hekim", "Veteriner Bey"), P("Hayvan Bakım Teknikeri")]),
    ("mezarlik", "Mezarlıklar Müdürlüğü", ["Mezarlıklar", "Cenaze Hizmetleri"], 430,
     [P("Mezarlıklar Müdürü"), R("Cenaze Hizmetleri", "Cenaze Nakil", "Cenaze Aracı")]),
]
ITFAIYE_RUTBE = [("İtfaiye Müdürü", 1), ("İtfaiye Amiri", 2), ("İtfaiye Çavuşu", 3), ("İtfaiye Eri", 3)]


class BelediyeRehberi:
    def __init__(self, seed: int = 2611) -> None:
        self.rng = random.Random(seed)
        self.kayitlar: list[dict[str, Any]] = []
        self.kullanilan: set[tuple[str, str]] = set()

    def _ad(self) -> tuple[str, str]:
        while True:
            ad = self.rng.choice(ERKEK_ADLARI if self.rng.random() < 0.6 else KADIN_ADLARI)
            soyad = self.rng.choice(SOYADLARI)
            if (ad, soyad) not in self.kullanilan and ad != soyad:
                self.kullanilan.add((ad, soyad))
                return ad, soyad

    def _ekle(self, kid: str, kind: str, birim: str, es: list[str], unvan: str, dahili: int, yol: list[str],
              kategori: str, aktarilabilir: bool = True, takma: list[str] | None = None) -> None:
        ad, soyad = self._ad() if kind == "person" else ("", "")
        self.kayitlar.append({
            "id": kid, "kind": kind, "first_name": ad, "last_name": soyad, "department": birim,
            "department_aliases": es, "title": unvan, "aliases": takma or [], "extension": str(dahili),
            "transferable": aktarilabilir, "birim_yolu": yol, "kategori": kategori, "updated_at": UPDATED_AT,
            "deleted": False})

    def uret(self) -> list[dict[str, Any]]:
        kok = [KURUM]
        self._ekle("b-kurum", "department", KURUM, ["Belediye", "Ilgınova Belediye", "Belediye Santrali"], "",
                   30000, kok, "kurum")
        self._ekle("b-baskan", "role", "Belediye Başkanlığı", ["Başkanlık", "Başkanlık Makamı"], "Belediye Başkanı",
                   30001, kok + ["Belediye Başkanlığı"], "makam", aktarilabilir=False, takma=["Başkan Bey", "Başkan"])
        for i in range(1, 4):
            self._ekle(f"b-baskan-yrd-{i}", "person", "Belediye Başkanlığı", ["Başkanlık"], "Başkan Yardımcısı",
                       30001 + i, kok + ["Belediye Başkanlığı"], "makam")
        self._ekle("b-ozel-kalem", "department", "Özel Kalem Müdürlüğü", ["Özel Kalem", "Başkanlık Sekreterliği"], "",
                   30010, kok + ["Özel Kalem Müdürlüğü"], "birim")
        self._ekle("b-ozel-kalem-mdr", "person", "Özel Kalem Müdürlüğü", ["Özel Kalem"], "Özel Kalem Müdürü", 30011,
                   kok + ["Özel Kalem Müdürlüğü"], "makam")
        self._ekle("b-cagri", "role", "Beyaz Masa", ["Çağrı Merkezi", "Halkla İlişkiler", "Şikayet Hattı"],
                   "Beyaz Masa", 30020, kok + ["Beyaz Masa"], "hizmet", takma=["Çağrı Merkezi", "Şikayet Hattı"])
        for anahtar, birim, es, bas, kadro in MUDURLUKLER:
            yol = kok + [birim]
            self._ekle(f"b-{anahtar}", "department", birim, es, "", bas * 100, yol, "birim")
            n = 1
            for unvan, kisi, aktar, takma in kadro:
                adet = 2 if unvan.endswith(("Memuru", "Mühendisi", "Teknikeri", "Avukat")) else 1
                for _ in range(adet):
                    self._ekle(f"b-{anahtar}-{n}", "person" if kisi else "role", birim, es, unvan, bas * 100 + n, yol,
                               "personel" if kisi else "hizmet", aktarilabilir=aktar, takma=takma)
                    n += 1
        # bağlı kuruluş: kısaltmalı su idaresi
        ilsu = "ILSU Su ve Kanalizasyon İdaresi"
        es = ["ILSU", "Su İdaresi", "Su ve Kanalizasyon"]
        self._ekle("b-ilsu", "department", ilsu, es, "", 50000, kok + [ilsu], "bagli_kurulus")
        for i, (unvan, kisi) in enumerate([("ILSU Genel Müdürü", True), ("Abone İşleri Şefi", True),
                                            ("Arıza Ekibi", False), ("Su Arıza Nöbetçisi", False),
                                            ("Kanal Ekibi Formeni", True)], start=1):
            self._ekle(f"b-ilsu-{i}", "person" if kisi else "role", ilsu, es, unvan, 50000 + i, kok + [ilsu],
                       "bagli_kurulus" if kisi else "hizmet")
        # itfaiye: rütbeler
        itf = "İtfaiye Daire Başkanlığı"
        es = ["İtfaiye", "İtfaiye Müdürlüğü"]
        self._ekle("b-itfaiye", "department", itf, es, "", 60000, kok + [itf], "birim")
        n = 1
        for unvan, adet in ITFAIYE_RUTBE:
            for _ in range(adet):
                self._ekle(f"b-itfaiye-{n}", "person", itf, es, unvan, 60000 + n, kok + [itf], "personel")
                n += 1
        self._ekle("b-itfaiye-santral", "role", itf, es, "İtfaiye Santrali", 60100, kok + [itf], "hizmet",
                   takma=["Yangın İhbar"])
        # silinmiş kayıt: aktarılmamalı
        self._ekle("b-eski", "person", "Fen İşleri Müdürlüğü", ["Fen İşleri"], "Eski Fen İşleri Müdürü", 31099,
                   kok + ["Fen İşleri Müdürlüğü"], "personel", aktarilabilir=False)
        self.kayitlar[-1]["deleted"] = True
        return self.kayitlar


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cikti", default=str(ROOT / "data/belediye_rehber.json"))
    ap.add_argument("--tohum", type=int, default=2611)
    a = ap.parse_args()
    kayitlar = BelediyeRehberi(a.tohum).uret()
    veri = {"_aciklama": "Görülmemiş kurum test rehberi (uydurma belediye). Eğitimde kullanılmaz.",
            "contacts": kayitlar}
    Path(a.cikti).write_text(json.dumps(veri, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    from collections import Counter
    print(f"{len(kayitlar)} kayıt → {a.cikti}", dict(Counter(k["kind"] for k in kayitlar)))


if __name__ == "__main__":
    main()
