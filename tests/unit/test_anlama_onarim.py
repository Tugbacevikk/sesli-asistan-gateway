"""LLM çıktısının sözle onarımı: arayanın kendi adı (D43) ve atlanan birim (D20). v7 ölçümü 23.11.2026."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from sesli_asistan.gateway.core.dialog import DialogAgent, DialogConfig
from sesli_asistan.gateway.core.dialog.understand import Understanding
from sesli_asistan.gateway.core.models import Contact
from sesli_asistan.gateway.core.ports import LlmError, LlmMessage
from sesli_asistan.gateway.core.search import DirectoryIndex
from sesli_asistan.gateway.core.tools import ToolRegistry

ROOT = Path(__file__).resolve().parents[2]


class MapLlm:
    def __init__(self, answers: dict[str, dict]):
        self.answers = answers

    async def chat(self, messages, tools):
        said = messages[-1]["content"]
        if said not in self.answers:
            raise LlmError("yok")
        return LlmMessage(json.dumps(self.answers[said], ensure_ascii=False))


def _konus(rehber: str, soz: str, cikti: dict) -> list[str]:
    data = json.loads((ROOT / rehber).read_text(encoding="utf-8"))
    index = DirectoryIndex(Contact.from_api(c) for c in data["contacts"])
    agent = DialogAgent(MapLlm({soz: cikti}), ToolRegistry(index), DialogConfig(understand="llm"))

    async def run() -> list[str]:
        ses = agent.new_session("t")
        ses.greeting()
        tr = await ses.handle_user(soz)
        return [a.extension for a in tr.actions if a.type == "transfer"]
    return asyncio.run(run())


def test_arayanin_kendi_adi_aranan_sanilmaz() -> None:
    # v7: "adım elif ... burak beyi" → ad elif, soyad burak (birim yok)
    soz = "adım elif muhasebeden burak beyi arıyorum"
    assert _konus("data/rehber_ornek.json", soz, {"niyet": "aktar", "ad": "elif", "soyad": "burak"}) == ["215"]


def test_sozde_gecen_birim_eklenir_silinmis_kiside_birim_hatti() -> None:
    # v7: "trafikten zafer geçmiş" → yalnız ad soyad; Zafer Geçmiş silinmiş, beklenen Trafik Şube hattı
    soz = "trafikten zafer geçmiş ile görüşecektim"
    assert _konus("data/demo_rehber.json", soz, {"niyet": "aktar", "ad": "zafer", "soyad": "geçmiş"}) == ["40200"]


def test_kendini_tanitma_yoksa_ad_korunur() -> None:
    got, notes = Understanding(niyet="aktar", ad="elif").repaired(
        "elif hanımla görüşecektim", frozenset({"elif"}), frozenset(), lambda b: False)
    assert got.ad == "elif" and not any("kendi adı" in n for n in notes)


# Sesli deneme v8 ölçümü 25.11.2026: LLM sözdeki bir bilgiyi atladı, kod eksik JSON'a güvendi.
def test_atlanan_unvan_birim_hattina_degil_makama() -> None:
    # S26: "dereköy kaymakamına" → yalnız birim; birim hattı 52000 değil kaymakam 52001
    soz = "selamünaleyküm gardaş dereköy kaymakamına bağla hele"
    assert _konus("data/demo_rehber.json", soz, {"niyet": "aktar", "birim": "dereköy kaymakamlığı"}) == ["52001"]


def test_unvan_yoksa_yalniz_birim_hatti() -> None:
    soz = "dereköy kaymakamlığına bağlar mısınız"
    assert _konus("data/demo_rehber.json", soz, {"niyet": "aktar", "birim": "dereköy kaymakamlığı"}) == ["52000"]


def test_makam_birimin_es_adiyla_dogrudan() -> None:
    # S21: "il jandarma komutanlığı" kurumun eş adı, komutan alt birimde (Makam): komutanın hattı, teyitsiz
    # (karar 26.11.2026: makam hatları aktarılabilir)
    soz = "il jandarma komutanlığının başındaki kişiyle görüşmek istiyorum"
    cikti = {"niyet": "aktar", "unvan": "komutan", "birim": "il jandarma komutanlığı"}
    assert _konus("data/demo_rehber.json", soz, cikti) == ["30001"]


def test_aktarilamayan_makamda_teyitsiz_birim_hatti(tmp_path: Path) -> None:
    # rehber bir makamı aktarılamaz işaretlerse: kural yolundaki gibi kurumun genel hattı, teyit sorulmaz
    data = json.loads((ROOT / "data/demo_rehber.json").read_text(encoding="utf-8"))
    for c in data["contacts"]:
        if c["id"] == "j-komutan":
            c["transferable"] = False
    rehber = tmp_path / "rehber.json"
    rehber.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    soz = "il jandarma komutanlığının başındaki kişiyle görüşmek istiyorum"
    cikti = {"niyet": "aktar", "unvan": "komutan", "birim": "il jandarma komutanlığı"}
    assert _konus(str(rehber), soz, cikti) == ["30000"]


def test_atlanan_birim_ust_birimle_karismaz() -> None:
    # J14: "jandarma personelden tolga koca" → yalnız ad soyad; birim Jandarma Personel eklenir (Komutanlık değil,
    # "Jandarma mı, Dereköy mü?" sorulmaz). "koca" ≠ "Koç": Tolga Koç teyit edilir, "evet" → 30204
    soz = "usagum jandarma personelden tolga koca verusun"
    data = json.loads((ROOT / "data/demo_rehber.json").read_text(encoding="utf-8"))
    index = DirectoryIndex(Contact.from_api(c) for c in data["contacts"])
    llm = MapLlm({soz: {"niyet": "aktar", "ad": "tolga", "soyad": "koca"}})
    agent = DialogAgent(llm, ToolRegistry(index), DialogConfig(understand="llm"))

    async def run():
        ses = agent.new_session("t")
        ses.greeting()
        first = await ses.handle_user(soz)
        second = await ses.handle_user("evet")
        return first, second
    first, second = asyncio.run(run())
    assert "Tolga Koç" in first.text and not first.actions
    assert [a.extension for a in second.actions if a.type == "transfer"] == ["30204"]


def test_kural_yolunda_aktarilamayan_makam_kurum_hattina(tmp_path: Path) -> None:
    # kural yolu da aynı: aktarılamaz işaretli vali → valiliğin genel hattı; yardımcısı adıyla istenirse o kişi
    data = json.loads((ROOT / "data/demo_rehber.json").read_text(encoding="utf-8"))
    for c in data["contacts"]:
        if c["id"] == "v-vali":
            c["transferable"] = False
    index = DirectoryIndex(Contact.from_api(c) for c in data["contacts"])
    agent = DialogAgent(MapLlm({}), ToolRegistry(index), DialogConfig(llm_enabled=False, understand="kural"))

    async def run(soz: str) -> list[str]:
        ses = agent.new_session("t")
        ses.greeting()
        tr = await ses.handle_user(soz)
        return [a.extension for a in tr.actions if a.type == "transfer"]
    assert asyncio.run(run("valiyle görüşmek istiyorum")) == ["20000"]
    assert asyncio.run(run("vali yardımcısı kemal yılmaz")) == ["20002"]


def test_konu_soylenince_genel_istek_sorulmaz() -> None:
    # deneme 26.11.2026: "cari hesap konusunda bilgi almak istiyorum" → "Hangi konuda?" yeniden soruluyordu
    from sesli_asistan.gateway.core.phrases import names_topic
    assert names_topic("cari hesap konusunda bilgi almak istiyorum")
    assert names_topic("pasaport ile ilgili soracaktım")
    assert not names_topic("bilgi almak istiyorum") and not names_topic("bu konuda bilgi almak istiyorum")
    soz = "cari hesap konusunda bilgi almak istiyorum"
    cikti = {"niyet": "konu", "konu": "cari hesap", "tahmini_birim": "Muhasebe"}
    data = json.loads((ROOT / "data/demo_rehber.json").read_text(encoding="utf-8"))
    index = DirectoryIndex(Contact.from_api(c) for c in data["contacts"])
    agent = DialogAgent(MapLlm({soz: cikti}), ToolRegistry(index), DialogConfig(understand="llm"))

    async def run() -> str:
        ses = agent.new_session("t")
        ses.greeting()
        return (await ses.handle_user(soz)).text
    # demo rehberinde muhasebe yok: "kişiyi bulamadım" değil, konu cevabı
    assert asyncio.run(run()).startswith("Bu konuyla ilgili birimi bulamadım")


def test_hal_hatir_hizli_yola_girmez() -> None:
    # deneme 26.11.2026: SMALL_TALK açıkken "günaydın nasılsınız" adımlara bırakıldı, hızlı yol "günaydın"ı "Aydın"
    # soyadı sandı ("Valilik mi, Dereköy mü?")
    data = json.loads((ROOT / "data/demo_rehber.json").read_text(encoding="utf-8"))
    index = DirectoryIndex(Contact.from_api(c) for c in data["contacts"])
    agent = DialogAgent(MapLlm({}), ToolRegistry(index), DialogConfig(understand="llm", small_talk=True))

    async def run(soz: str) -> tuple[str, list[str]]:
        ses = agent.new_session("t")
        ses.greeting()
        tr = await ses.handle_user(soz)
        return tr.text, [a.extension for a in tr.actions if a.type == "transfer"]
    for soz in ("günaydın nasılsınız", "merhaba nasılsınız bugün hava çok güzel"):
        text, ext = asyncio.run(run(soz))
        assert "mi," not in text and not ext and text.endswith("kiminle görüşmek istersiniz?"), (soz, text)
    assert asyncio.run(run("günaydın hasan aydın ile görüşmek istiyorum"))[1] == ["20032"]


def test_konu_listesine_uymayan_tahmin_onerilmez() -> None:
    # deneme 26.11.2026: demo rehberinde muhasebe yok, LLM "cari hesap" için İl Milli Eğitim Müdürlüğü'nü önerdi;
    # konu listesi bu konunun birimini biliyor (muhasebe / mali işler): uymayan öneri sorulmaz
    from sesli_asistan.gateway.composition import load_topics
    soz = "cari hesap konusunda bilgi almak istiyorum"

    def cevap(rehber: str, birim: str) -> str:
        data = json.loads((ROOT / rehber).read_text(encoding="utf-8"))
        index = DirectoryIndex(Contact.from_api(c) for c in data["contacts"])
        llm = MapLlm({soz: {"niyet": "konu", "konu": "cari hesap", "tahmini_birim": birim}})
        agent = DialogAgent(llm, ToolRegistry(index, topics=load_topics(None)), DialogConfig(understand="llm"))

        async def run() -> str:
            ses = agent.new_session("t")
            ses.greeting()
            return (await ses.handle_user(soz)).text
        return asyncio.run(run())
    assert cevap("data/demo_rehber.json", "İl Milli Eğitim Müdürlüğü").startswith("Bu konuyla ilgili birimi bulamadım")
    assert cevap("data/sirket_rehber.json", "Muhasebe") == "Muhasebe'ye mi bağlamamı istersiniz?"


def test_secenek_sorusunda_bozuk_yer_adi_teyitle() -> None:
    # sesli deneme 26.11.2026: "Akçakaya mı, Dereköy mü?" → "Dereköy" STT'de "dea kör"; eşlenmedi, kısık diye arka
    # plan da sayıldı, çağrı operatöre gitti
    data = json.loads((ROOT / "data/demo_rehber.json").read_text(encoding="utf-8"))
    index = DirectoryIndex(Contact.from_api(c) for c in data["contacts"])
    agent = DialogAgent(MapLlm({}), ToolRegistry(index), DialogConfig(understand="kural", llm_enabled=False))

    async def run(cevap: str) -> tuple[str, bool, list[str]]:
        ses = agent.new_session("t")
        ses.greeting()
        assert (await ses.handle_user("nüfus müdürüyle görüşmek istedim")).text == "Akçakaya mı, Dereköy mü?"
        ilgili = ses.concerns_call(cevap)
        ilk = await ses.handle_user(cevap)
        if ilk.actions:
            return ilk.text, ilgili, [a.extension for a in ilk.actions if a.type == "transfer"]
        tr = await ses.handle_user("evet")
        return ilk.text, ilgili, [a.extension for a in tr.actions if a.type == "transfer"]
    soru, ilgili, ext = asyncio.run(run("dea kör"))
    assert soru == "Dereköy Nüfus Müdürüne mi bağlamamı istersiniz?" and ilgili and ext == ["52021"]
    assert asyncio.run(run("dere köy"))[2] == ["52021"]          # bitişik yazılınca doğrudan
    soru, ilgili, _ = asyncio.run(run("tekrar"))
    assert soru.startswith("Akçakaya mı, Dereköy mü?") and not ilgili   # ilgisiz söz: seçenek uydurulmaz


def test_secenek_cevabi_anlasilmazsa_sayiyla_secilir() -> None:
    # sesli deneme 26.11.2026: "Dereköy" defalarca "ne kör bir dekar", "bir sorumuz var" duyuldu; sayı sağlam duyulur
    from sesli_asistan.gateway.core.phrases import number_answer
    assert number_answer(["iki"]) == 1 and number_answer(["2"]) == 1
    assert number_answer(["bir", "sorumuz", "var"]) is None
    data = json.loads((ROOT / "data/demo_rehber.json").read_text(encoding="utf-8"))
    index = DirectoryIndex(Contact.from_api(c) for c in data["contacts"])
    agent = DialogAgent(MapLlm({}), ToolRegistry(index), DialogConfig(understand="kural", llm_enabled=False))

    async def run(*sozler: str) -> list[tuple[str, list[str]]]:
        ses = agent.new_session("t")
        ses.greeting()
        out = []
        for soz in sozler:
            tr = await ses.handle_user(soz)
            out.append((tr.text, [a.extension for a in tr.actions if a.type == "transfer"]))
        return out
    out = asyncio.run(run("kaymakamla görüşmek istiyorum", "ne kör bir dekar", "iki"))
    assert out[0][0] == "Akçakaya mı, Dereköy mü?"
    assert out[1] == ("Akçakaya mı, Dereköy mü? Birinci ya da ikinci diyebilirsiniz.", [])
    assert out[2][1] == ["52001"]
    assert asyncio.run(run("kaymakamla görüşmek istiyorum", "bir sorumuz var", "1"))[2][1] == ["51001"]
    assert asyncio.run(run("kaymakamla görüşmek istiyorum", "ne kör bir dekar", "ikinci"))[2][1] == ["52001"]


def test_teyit_yinelenince_ayni_ad_ve_tamlama() -> None:
    # sesli deneme 26.11.2026: "dene karşımız" → "Dereköy Kaymakama mı…" (eksiz), "tekrar" → yinelemesi kişinin
    # adıyla "Okan Yıldırım'a mı…" soruldu
    data = json.loads((ROOT / "data/demo_rehber.json").read_text(encoding="utf-8"))
    index = DirectoryIndex(Contact.from_api(c) for c in data["contacts"])
    agent = DialogAgent(MapLlm({}), ToolRegistry(index), DialogConfig(understand="kural", llm_enabled=False))

    async def run() -> list[str]:
        ses = agent.new_session("t")
        ses.greeting()
        out = [(await ses.handle_user(x)).text for x in ("kaymakamla görüşmek istiyorum", "dea kör", "tekrar")]
        tr = await ses.handle_user("evet")
        return [*out, *(a.extension for a in tr.actions if a.type == "transfer")]
    out = asyncio.run(run())
    assert out[1] == "Dereköy Kaymakamına mı bağlamamı istersiniz?"
    assert out[2].startswith("Anlayamadım. Dereköy Kaymakamına mı") and out[3] == "52001"
