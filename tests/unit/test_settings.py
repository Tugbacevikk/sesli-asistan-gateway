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
