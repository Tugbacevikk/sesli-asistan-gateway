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
