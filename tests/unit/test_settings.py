"""Ayar ekranı (tasarim.md 13.1): doğrulama, anında uygulama, kalıcılık, erişim."""
import json
import os

import pytest
from test_ui import ROOT, client

from sesli_asistan.gateway.adapters.settings.json_store import LocalSettingsStore
from sesli_asistan.gateway.composition import GatewayRuntime
from sesli_asistan.gateway.config import GatewaySettings
from sesli_asistan.gateway.core.models import Match

JSON = {"Content-Type": "application/json"}


def put(tc, body, headers=JSON):
    return tc.put("/v1/admin/settings", content=json.dumps(body), headers=headers)


def test_anons_mesai_ve_takma_ad_aninda_uygulanir():
    tc, rt = client()
    with tc:
        st = tc.get("/v1/admin/settings").json()
        assert st["writable"] and {t["name"] for t in st["texts"]} >= {"greeting", "closed", "no_operator"}
        assert any(c["id"] == "c-101" for c in st["contacts"])
        assert rt.directory.index.search("ferhat bey").match == Match.NONE
        r = put(tc, {"texts": {"greeting": "Hoş geldiniz, kimi arıyorsunuz?", "error": ""},
                     "office_hours": "pzt-cum 08:00-17:00", "after_hours_mode": "assistant",
                     "aliases": {"c-101": ["Ferhat Bey", "ferhat bey", " "]}})
        assert r.status_code == 200, r.text
        v = r.json()
        assert v["missing"] == [] and v["aliases"] == {"c-101": ["Ferhat Bey"]}
        greet = next(t for t in v["texts"] if t["name"] == "greeting")
        assert greet["changed"] and greet["value"].endswith("kimi arıyorsunuz?")
        assert next(t for t in v["texts"] if t["name"] == "error")["changed"] is False   # boş: varsayılan
        # anında: diyalog, hazır ses, mesai, arama
        assert rt.agent.fixed_prompts()["greeting"] == "Hoş geldiniz, kimi arıyorsunuz?"
        assert rt.speaker.library.by_text("Hoş geldiniz, kimi arıyorsunuz?") is not None   # yeniden seslendirildi
        assert rt.after_hours_mode == "assistant" and not rt.office.always_open
        hit = rt.directory.index.search("ferhat bey")
        assert hit.match == Match.SINGLE_HIGH and hit.candidates[0].contact.id == "c-101"
        assert rt._call_config().after_hours_mode == "assistant"


def test_kalici_ve_rehber_yenilenince_korunur(tmp_path):
    path = str(tmp_path / "ayar.json")
    s = GatewaySettings(_env_file=None, directory_path=str(ROOT / "data/rehber_ornek.json"), local_settings_file=path)
    LocalSettingsStore(path).save({"texts": {"closed": "Kapalıyız."}, "aliases": {"c-101": ["Ferhat Bey"]},
                                   "holidays": "01-01"})
    rt = GatewayRuntime(s, llm=object(), stt=object(), tts=object())
    assert rt.agent.cfg.texts.closed == "Kapalıyız." and rt.agent.fixed_prompts()["closed"] == "Kapalıyız."
    assert not rt.office.always_open

    async def go():
        await rt.directory.refresh()
        assert rt.directory.index.search("ferhat bey").candidates[0].contact.id == "c-101"
        rt.directory.index.rebuild(rt.directory.index._contacts)      # rehber güncellemesi
        assert rt.directory.index.search("ferhat bey").candidates[0].contact.id == "c-101"
    import asyncio
    asyncio.run(go())
    rt.history.close()


@pytest.mark.parametrize("body,msg", [
    ({"texts": {"transferring": "Aktarıyorum."}}, "Kişiye aktarma şunu içermeli"),
    ({"texts": {"greeting": "Merhaba {firma}"}}, "Karşılama: geçersiz yer tutucu"),
    ({"texts": {"error": "Hata {x}"}}, "yer tutucu almaz"),
    ({"texts": {"yok": "x"}}, "bilinmeyen anons"),
    ({"office_hours": "pzt 8-18"}, "Mesai kuralı"),
    ({"holidays": "2026-13-01"}, "Mesai kuralı"),
    ({"after_hours_mode": "sessiz"}, "announce ya da assistant"),
    ({"aliases": {"c-yok": ["X"]}}, "rehberde olmayan"),
    ({"aliases": {"c-101": ["Dr."]}}, "takma ad"),
    ({"aliases": {"c-101": [f"ad{i}" for i in range(11)]}}, "en çok 10"),
])
def test_hatali_ayar_kaydedilmez(body, msg, tmp_path):
    tc, rt = client()
    with tc:
        before = rt.agent.cfg.texts
        r = put(tc, body)
        assert r.status_code == 400 and msg in r.json()["error"]["message"], r.text
        assert rt.agent.cfg.texts == before
        assert not os.path.exists(os.environ["LOCAL_SETTINGS_FILE"])


def test_yalniz_json_ve_erisim_kurali():
    tc, _ = client(key="gizli")
    with tc:
        assert tc.get("/v1/admin/settings").status_code == 401
        auth = {"Authorization": "Bearer gizli"}
        assert put(tc, {}, headers={**auth, "Content-Type": "text/plain"}).status_code == 415   # form/CSRF
        assert put(tc, {}, headers={**auth, **JSON}).status_code == 200


def test_bozuk_dosya_kenara_alinir(tmp_path):
    p = tmp_path / "ayar.json"
    p.write_text("{bozuk", encoding="utf-8")
    assert LocalSettingsStore(str(p)).load() == {}
    assert not p.exists() and (tmp_path / "ayar.json.bozuk").exists()


def test_dosya_yoksa_salt_okunur():
    s = GatewaySettings(_env_file=None, directory_path=str(ROOT / "data/rehber_ornek.json"), local_settings_file="")
    rt = GatewayRuntime(s, llm=object(), stt=object(), tts=object())
    assert rt.settings.view()["writable"] is False
    with pytest.raises(ValueError, match="salt okunur"):
        import asyncio
        asyncio.run(rt.settings.update({}))
    rt.history.close()


def test_rehbere_yerel_kayit_eklenir_bulunur_silinir():
    tc, rt = client()
    with tc:
        assert rt.directory.index.search("ferhat yıldırım").match == Match.NONE
        r = tc.post("/v1/admin/contacts", content=json.dumps({"first_name": "Ferhat", "last_name": "Yıldırım",
                                                              "department": "Bilgi İşlem", "extension": "246"}),
                    headers=JSON)
        assert r.status_code == 201, r.text
        local = next(c for c in r.json()["contacts"] if c["local"])
        assert local["name"] == "Ferhat Yıldırım" and local["department"] == "Bilgi İşlem"
        assert local["id"].startswith("yerel-")
        hit = rt.directory.index.search("ferhat yıldırım")
        assert hit.match == Match.SINGLE_HIGH and hit.candidates[0].contact.extension == "246"
        assert "Ferhat Yıldırım" in rt._hotwords()
        # takma ad yerel kayda da verilebilir; Ayarlar kaydı yerel kayıtları silmez
        r = put(tc, {"aliases": {local["id"]: ["Ferhat Bey"]}})
        assert r.status_code == 200 and any(c["local"] for c in r.json()["contacts"])
        assert rt.directory.index.search("ferhat bey").candidates[0].contact.extension == "246"
        # rehber kaynaktan yenilenince yerel kayıt durur
        rt.directory.index.rebuild(rt.directory.index._contacts)
        assert rt.directory.index.search("ferhat yıldırım").match == Match.SINGLE_HIGH
        # birim hattı
        r = tc.post("/v1/admin/contacts", content=json.dumps({"kind": "department", "department": "Güvenlik",
                                                              "extension": "199", "first_name": "x"}), headers=JSON)
        assert r.status_code == 201
        g = next(c for c in r.json()["contacts"] if c["extension"] == "199")
        assert g["kind"] == "department" and g["name"] == "Güvenlik"
        # silme: takma adı da gider; kaynak rehber kaydı silinemez
        assert tc.delete(f"/v1/admin/contacts/{local['id']}").status_code == 200
        assert rt.directory.index.search("ferhat yıldırım").match == Match.NONE
        assert local["id"] not in rt.settings.local.get("aliases", {})
        assert tc.delete("/v1/admin/contacts/c-101").status_code == 400


@pytest.mark.parametrize("body,msg", [
    ({"first_name": "Ali", "last_name": "Veli", "extension": "214"}, "zaten Ayşe Yılmaz kaydında"),
    ({"first_name": "Ali", "last_name": "Veli", "extension": "05321234567"}, "2–6 rakam"),
    ({"first_name": "Ali", "last_name": "Veli", "extension": "9X1"}, "2–6 rakam"),
    ({"first_name": "Ali", "extension": "250"}, "ad ve soyad"),
    ({"first_name": "Ayşe", "last_name": "Yılmaz", "extension": "250"}, "zaten var"),
    ({"kind": "department", "extension": "251"}, "birim adı"),
    ({"kind": "robot", "department": "X", "extension": "252"}, "kişi ya da birim"),
])
def test_hatali_yerel_kayit_reddedilir(body, msg):
    tc, rt = client()
    with tc:
        r = tc.post("/v1/admin/contacts", content=json.dumps(body), headers=JSON)
        assert r.status_code == 400 and msg in r.json()["error"]["message"], r.text
        assert not any(c.id.startswith("yerel-") for c in rt.directory.index.contacts())


def test_yerel_kayit_kalici(tmp_path):
    path = str(tmp_path / "ayar.json")
    LocalSettingsStore(path).save({"contacts": [{"id": "yerel-0000abcd", "first_name": "Ferhat",
                                                 "last_name": "Yıldırım", "department": "Bilgi İşlem",
                                                 "extension": "246", "kind": "person", "title": ""}]})
    s = GatewaySettings(_env_file=None, directory_path=str(ROOT / "data/rehber_ornek.json"), local_settings_file=path)
    rt = GatewayRuntime(s, llm=object(), stt=object(), tts=object())
    import asyncio
    asyncio.run(rt.directory.refresh())
    assert rt.directory.index.search("ferhat yıldırım").candidates[0].contact.id == "yerel-0000abcd"
    rt.history.close()


def test_llm_kapali_saglikta_yok_ve_hazir():
    import asyncio

    from test_ui import Stt, Tts
    s = GatewaySettings(_env_file=None, directory_path=str(ROOT / "data/rehber_ornek.json"), llm_enabled=False)

    class Patlayan:
        async def chat(self, *a):
            raise AssertionError("LLM kapalıyken çağrıldı")

    rt = GatewayRuntime(s, llm=Patlayan(), stt=Stt(), tts=Tts())

    async def go():
        await rt.start()
        try:
            h = rt.health()
            assert "llm" not in h["components"] and h["status"] == "ok"
            t = await rt.agent.new_session("x").handle_user("zeynep lütfen")
            assert [a.extension for a in t.actions] == ["260"] and t.llm_calls == 0
        finally:
            await rt.stop()
    asyncio.run(go())


def test_yeni_anonslar_ayar_dogrulamasi():
    with pytest.raises(ValueError, match="BUSY_TEXT şunu içermeli"):
        GatewaySettings(_env_file=None, busy_text="Meşgul.")
    with pytest.raises(ValueError, match="yer tutucu almaz"):
        GatewaySettings(_env_file=None, not_found_text="{ad} yok")
    assert GatewaySettings(_env_file=None, busy_text="{ad} hatta.").dialog_texts().busy == "{ad} hatta."
