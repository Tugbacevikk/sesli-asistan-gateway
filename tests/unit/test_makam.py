"""Makam hatlı rehber (ADR-030): UTF-16 döküm, kısaltma açılımı, makam hattı türü, unvanla arama, aynı unvanlı
çok hatta birimle süzme, bozuk birim / konu, büyük rehber. Rehber sahte: tests/data/makam_rehber.tsv (gerçek veri yok).
makam_rehber.json bu dosyanın içe aktarılmış hâlidir (test_json_tsv_ile_ayni yeniden üretilip karşılaştırır)."""
import asyncio
import json
from collections import Counter
from pathlib import Path

import pytest

from sesli_asistan.gateway.abbreviations import Abbreviations, load_abbreviations
from sesli_asistan.gateway.composition import load_topics
from sesli_asistan.gateway.core.dialog import DialogAgent, DialogConfig
from sesli_asistan.gateway.core.models import Contact
from sesli_asistan.gateway.core.phrases import common_dative
from sesli_asistan.gateway.core.search import DirectoryIndex
from sesli_asistan.gateway.core.tools import ToolRegistry
from sesli_asistan.gateway.directory_import import looks_like_person, parse, read_table

ROOT = Path(__file__).resolve().parents[2]
TSV = ROOT / "tests/data/makam_rehber.tsv"


def imported():
    data = TSV.read_text(encoding="utf-8").encode("utf-16")      # santral dökümü gibi: UTF-16, sekmeli
    return parse(read_table(data, "rehber.csv"), (), load_abbreviations())


class NoLlm:
    async def chat(self, *a, **k):
        raise AssertionError("LLM'e gidilmemeli")


def agent(mcp_step=True):
    res = imported()
    ix = DirectoryIndex(Contact.from_api(c) for c in res.contacts)
    return DialogAgent(NoLlm(), ToolRegistry(ix, topics=load_topics()),
                       DialogConfig(organization="Deneme", llm_enabled=False, mcp_step=mcp_step))


def say(a, *texts):
    s = a.new_session("m")
    turn = None
    for x in texts:
        turn = asyncio.run(s.handle_user(x))
    return turn


# --- kısaltmalar ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("raw", "full"), [
    ("Asyş.D.Bşk.lığı Önl.Hiz.Şb.Md.", "Asayiş Daire Başkanlığı Önleyici Hizmetler Şube Müdürü"),
    ("Bşk.Yrd. Belgegeçer", "Başkan Yardımcısı Belgegeçer"),          # bileşik anahtar önce
    ("Per.D.Bşk.", "Personel Daire Başkanı"),
    ("Salon Sorumlusu", "Salon Sorumlusu"),
])
def test_genel_sozlukle_acilim(raw, full):
    assert load_abbreviations().expand(raw) == full


def test_bilinmeyen_kisaltma_kalir_ve_sayilir():
    unknown: Counter[str] = Counter()
    assert load_abbreviations().expand("Des.Kt.Grp.K.lığı", unknown) == "Destek Kt. Grup Komutanlığı"
    assert unknown == {"Kt.": 1}


def test_kurumun_sozlugu_geneli_ezer(tmp_path):
    f = tmp_path / "k.json"
    f.write_text(json.dumps({"kisaltmalar": {"Kt.": "Kıta", "D.": "Dairesi"}}), encoding="utf-8")
    a = load_abbreviations(str(f))
    assert a.expand("Des.Kt.Grp.") == "Destek Kıta Grup" and a.expand("D.") == "Dairesi"
    assert Abbreviations({"BŞK.": "Başkanı"}).expand("bşk.") == "Başkanı"        # harf büyüklüğü duyarsız


# --- içe aktarma -----------------------------------------------------------------------------------------------

def test_utf16_dokum_makam_hatlari_ve_kisiler():
    res = imported()
    kinds = Counter(c["kind"] for c in res.contacts)
    assert kinds == {"role": 25, "person": 2} and not res.errors
    assert res.skipped == 4                                       # 2 yalnız kod + 2 tekrar
    by_ext = {c["extension"]: c for c in res.contacts}
    assert by_ext["21021"]["title"] == "Asayiş Daire Başkanlığı Önleyici Hizmetler Şube Müdürü"
    assert by_ext["21021"]["department"] == "Asayiş Başkanlığı"
    assert by_ext["21002"]["transferable"] is False               # belgegeçer
    assert by_ext["21062"]["first_name"] == "Deniz" and by_ext["21062"]["last_name"] == "Aksoy"
    msgs = " | ".join(p.message for p in res.warnings)
    assert "açılımı bilinmeyen" in msgs and "Atm." in msgs and "tekrarı" in msgs


def test_json_tsv_ile_ayni():
    # tests/data/makam_rehber.json yeniden üretilebilir olmalı (zaman damgası hariç)
    stored = json.loads((ROOT / "tests/data/makam_rehber.json").read_text(encoding="utf-8"))["contacts"]
    fresh = imported().contacts
    strip = [{k: v for k, v in c.items() if k != "updated_at"} for c in fresh]
    assert [{k: v for k, v in c.items() if k != "updated_at"} for c in stored] == strip


@pytest.mark.parametrize(("text", "person"), [
    ("Deniz Aksoy", True), ("Ayşe Nur Kaya", True), ("Salon Sorumlusu", False), ("Nizamiye", False),
    ("Şube Müdürü", False), ("Santral Kapı", False), ("Asb. Ali", False),
])
def test_kisi_adi_mi_makam_mi(text, person):
    assert looks_like_person(text) is person


@pytest.mark.parametrize(("phrase", "dat"), [
    ("Şube Müdürü", "Şube Müdürüne"), ("Salon Sorumlusu", "Salon Sorumlusuna"), ("Nizamiye", "Nizamiyeye"),
    ("Hukuk Avukat", "Hukuk Avukata"), ("Personel Daire Başkanı", "Personel Daire Başkanına"),
])
def test_makam_yonelme_eki(phrase, dat):
    assert common_dative(phrase) == dat


# --- arama ve diyalog --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("mcp_step", [True, False])
@pytest.mark.parametrize(("text", "ext"), [
    ("personel daire başkanı", "21010"), ("özlük şube müdürü", "21013"),
    ("asayiş önleyici hizmetler şube müdürü", "21021"), ("yazılım geliştirme şube müdürü", "21050"),
    ("başkan yardımcısı", "21001"), ("özel kalem müdürü", "21003"), ("hukuk müşavirliğinden avukat", "21041"),
    ("salon sorumlusu", "21060"), ("teftiş kurulu başkanı", "21030"), ("deniz aksoy", "21062"),
])
def test_unvanla_makam_hattina(text, ext, mcp_step):
    t = say(agent(mcp_step), text)
    assert [x.extension for x in t.actions] == [ext], t.text


def test_aktarma_cumlesi_cins_isimle():
    assert say(agent(), "özlük şube müdürü").text == ("Sizi Personel Daire Başkanlığı Özlük Şube Müdürüne "
                                                     "aktarıyorum.")


def test_ayni_unvanli_cok_hat_birimle_suzulur():
    a = agent()
    q = say(a, "müfettiş ile görüşmek istiyorum")
    assert not q.actions and q.text == "Hangi birimdeki müfettiş?"
    assert [x.extension for x in say(a, "müfettiş ile görüşmek istiyorum", "hukuk müşavirliği").actions] == ["21040"]
    t = say(a, "müfettiş ile görüşmek istiyorum", "teftiş kurulundan")      # 3 aynı hat: ayırt edilemez
    assert [x.type for x in t.actions] == ["operator"]
    t = say(a, "şube müdürü", "personel", "özlük")
    assert [x.extension for x in t.actions] == ["21013"]


def test_ayirt_edilemeyen_ayni_hatlar_operatore():
    t = say(agent(), "teftiş kurulu müfettişi")
    assert [x.type for x in t.actions] == ["operator"] and "birden fazla hat" in t.text


def test_belgegecer_aktarilmaz():
    t = say(agent(), "belgegeçer")
    assert all(getattr(x, "extension", "") != "21002" for x in t.actions)


# --- bozuk duyulan birim / konu ve konu çelişkisi (örnek rehber) ------------------------------------------------

def _sample():
    data = json.loads((ROOT / "data/rehber_ornek.json").read_text(encoding="utf-8"))
    ix = DirectoryIndex(Contact.from_api(c) for c in data["contacts"])
    return DialogAgent(NoLlm(), ToolRegistry(ix, topics=load_topics()),
                       DialogConfig(organization="Deneme", llm_enabled=False, mcp_step=True))


@pytest.mark.parametrize(("text", "question"), [
    ("masebeye bağlar mısınız", "Muhasebe'ye mi bağlamamı istersiniz?"),          # STT: muhasebeye
    ("çifremi unuttum", "Can Öztürk'e mi bağlamamı istersiniz?"),                  # STT: şifremi → Bilgi İşlem
    ("zeynep faturam ile ilgili bir sorun var zeynep bey", "Zeynep Arslan mı, Muhasebe mi?"),
    ("teknik şifremi unuttum", "Teknik Destek mi, Can Öztürk mü?"),
])
def test_bozuk_birim_ve_konu_celiskisi_sorulur(text, question):
    t = say(_sample(), text)
    assert not t.actions and t.text == question


@pytest.mark.parametrize(("text", "ext"), [("personel ile görüşmek istiyorum", "260"), ("bilgi işlem", "245")])
def test_dogru_birim_bulanik_eslemeye_girmez(text, ext):
    assert [x.extension for x in say(_sample(), text).actions] == [ext]


def test_buyuk_rehber_hizli():
    # 3.000 makam hattı: önbellekli tablolar; söz başına süre sınırlı (ölçüm 05.11.2026: önbelleksiz 0,5–1 sn)
    import time
    units = ["Asayiş", "Personel", "Lojistik", "Eğitim", "Hukuk", "İstihbarat", "Siber", "Plan", "Destek", "Kalite"]
    roles = ["Şube Müdürü", "Uzmanı", "Astsubayı", "Belgegeçer", "Büro Sorumlusu", "Kısım Amiri"]
    cs = [Contact.from_api({"id": f"r{i}", "kind": "role", "first_name": "", "last_name": "",
                            "department": f"{units[i % 10]} Başkanlığı",
                            "title": f"{units[i % 10]} Daire Başkanlığı {i // 60}. Şube {roles[i % 6]}",
                            "extension": str(30000 + i)}) for i in range(3000)]
    a = DialogAgent(NoLlm(), ToolRegistry(DirectoryIndex(cs), topics=load_topics()),
                    DialogConfig(organization="D", llm_enabled=False, mcp_step=True))
    say(a, "ısınma")
    t0 = time.perf_counter()
    for x in ("asayiş şube müdürü", "lojistik uzmanı", "hukuk kısım amiri", "falanca filanca"):
        say(a, x)
    assert (time.perf_counter() - t0) / 4 < 1.5


# --- birim belli, kişi değil -----------------------------------------------------------------------------------

def test_hatti_olmayan_birimde_kiminle_sorulur_ve_cevap_birimin_icinden_secilir():
    a = agent()
    s = a.new_session("m")
    t = asyncio.run(s.handle_user("personel başkanlığı"))
    assert t.text == "Personel Başkanlığından kiminle görüşmek istersiniz?" and not t.actions
    t = asyncio.run(s.handle_user("daire başkanı"))         # şube müdürlerinin unvanında da "daire başkanlığı" var
    assert [x.extension for x in t.actions] == ["21010"]


def test_hatti_olmayan_birimde_sube_muduru_secilir():
    t = say(agent(), "personel başkanlığı", "özlük şube müdürü")
    assert [x.extension for x in t.actions] == ["21013"]
