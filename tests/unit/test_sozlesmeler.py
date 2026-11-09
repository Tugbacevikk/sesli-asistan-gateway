"""Güvenlik ağı (yeniden düzenleme öncesi, 09.11.2026): modüller bölünürken ve sözlükler tipli sözleşmelere
çevrilirken dışarıdan görülen davranış değişmesin.

Testler iç yapıya değil, dışarıya verilen sözleşmeye bakar:
  1. Genel ad yüzeyi: başka modüllerin / araçların içe aktardığı adlar aynı yoldan alınabilir (bölme adımları).
  2. Çağrı gözlemcisi: arayüze (canlı izleme) giden olay türleri ve alanları (sabitlere çevirme adımı).
  3. Ayar ekranı: GET /v1/admin/settings cevabının biçimi (LocalSettings / SettingsView adımı).
  4. Rehber yükleme durumu: arayüzün okuduğu alanlar (rehber kaydı arayüzü adımı).
  5. Operatöre aktarma, arayan söze girse de yapılır (CallSession ayırma adımı; modül belgesindeki kural).
Dış bağımlılıklar (STT, TTS, LLM, santral) test_call / test_ui'deki sahtelerle yalıtılır; ağ ve gerçek dosya yok
(rehber yükleme testi geçici klasör kullanır).
"""
import asyncio
import importlib
from dataclasses import fields

import pytest
from test_call import Call, Llm, tool
from test_directory_import import client as upload_client
from test_ui import client as ui_client

from sesli_asistan.gateway.core.call import CallState, TurnRecord
from sesli_asistan.gateway.core.ports import LlmError
from sesli_asistan.gateway.live import KINDS

# --- 1. genel ad yüzeyi -----------------------------------------------------------------------------------

PUBLIC = {
    "sesli_asistan.gateway.core.people": [
        "department_said", "fuzzy_departments", "has_honorific", "mentioned_targets", "narrow_candidates",
        "resolve_person", "said_surely", "same_title", "same_unit", "sound_alike"],
    "sesli_asistan.gateway.core.call": [
        "CallConfig", "CallSession", "CallState", "CallStats", "TRANSFER_STATUSES", "TurnRecord"],
    "sesli_asistan.gateway.core.dialog": [
        "DialogAgent", "DialogConfig", "DialogSession", "DialogTexts", "FEW_SHOT", "GREETING_NO_ORG", "STEPS",
        "SYSTEM_PROMPT", "clean_reply", "complete_sentences", "fixed_prompts"],
    "sesli_asistan.gateway.directory_import": [
        "MAX_FILE", "diff", "export_csv", "fold", "keep_unchanged_timestamps", "looks_like_person", "merge",
        "parse", "read_table", "template_csv"],
    "sesli_asistan.gateway.settings_service": ["SettingsService", "TEXT_LABELS"],
    "sesli_asistan.gateway.api.ui": ["UI_PREFIXES", "mount_ui", "request_key"],
    "sesli_asistan.gateway.composition": ["GatewayRuntime", "load_topics"],
}


@pytest.mark.parametrize("module", sorted(PUBLIC))
def test_genel_adlar_ayni_yoldan_alinir(module):
    mod = importlib.import_module(module)
    missing = [n for n in PUBLIC[module] if not hasattr(mod, n)]
    assert not missing, f"{module}: {missing}"


# --- 2. çağrı gözlemcisi ----------------------------------------------------------------------------------

async def test_gozlemci_olaylari_ve_alanlari():
    seen: list[tuple[str, dict]] = []
    async with Call(Llm(tool("InContacts", Name="Ayşe", Department="muhasebe"))) as c:
        c.session.observer = lambda k, d: seen.append((k, d))
        c.say("muhasebeden ayşe hanım")
        await c.until(lambda: c.pbx.actions)
        c.session.transfer_result("answered")
        await c.until(lambda: c.session.state == CallState.HANDED_OFF)
    kinds = [k for k, _ in seen]
    # arayüze giden her tür canlı izlemede tanınır (yalnız klavye sesi bilgisi süzülür)
    assert set(kinds) - {"thinking_sound"} <= KINDS
    # sıra: duyulan → cevap → tur özeti → santral eylemi (cümle bitince); en sonda bitiş
    order = [kinds.index(k) for k in ("heard", "reply", "turn", "action")]
    assert order == sorted(order) and kinds[-1] == "ended"
    first = {k: d for k, d in reversed(seen)}             # her türün ilk olayı
    assert first["heard"]["text"] == "muhasebeden ayşe hanım" and "confidence" in first["heard"]
    assert first["reply"]["text"]
    assert first["action"] == {"action": "transfer", "extension": "214"}
    assert set(first["turn"]) == {f.name for f in fields(TurnRecord)}
    assert {d["state"] for k, d in seen if k == "state"} <= {s.value for s in CallState}
    assert seen[-1][1] == {"outcome": c.stats.outcome, "reason": c.stats.reason}


# --- 3. ayar ekranı ---------------------------------------------------------------------------------------

def test_ayar_ekrani_cevap_bicimi():
    tc, _ = ui_client()
    with tc:
        v = tc.get("/v1/admin/settings").json()
    assert set(v) == {"writable", "user", "texts", "office", "aliases", "contacts", "prompts_missing",
                      "directory_upload"}
    assert set(v["user"]) == {"name", "role"}
    assert all(set(t) == {"name", "label", "placeholders", "value", "default", "changed"} for t in v["texts"])
    assert set(v["office"]) == {"hours", "holidays", "mode", "status", "timezone"}
    assert v["contacts"] and all(set(c) == {"id", "name", "department", "title", "extension", "kind", "aliases",
                                            "local", "duplicate"} for c in v["contacts"])
    assert isinstance(v["aliases"], dict) and isinstance(v["prompts_missing"], bool)


# --- 4. rehber yükleme durumu -----------------------------------------------------------------------------

def test_rehber_yukleme_durumu_alanlari(tmp_path):
    tc, _, path = upload_client(tmp_path)
    with tc:
        st = tc.get("/v1/admin/settings").json()["directory_upload"]
    assert {"enabled", "file", "revertible"} <= set(st) <= {"enabled", "file", "revertible", "modified", "reason"}
    assert st["enabled"] is True and st["revertible"] is False and st["file"] == str(path)


# --- 5. operatöre aktarma söze girilse de yapılır ---------------------------------------------------------

async def test_operator_cumlesine_soze_girilse_de_operatore_aktarilir():
    async with Call(Llm(LlmError("bağlantı yok")), play_s=0.3) as c:
        c.say("ayşe")
        await c.until(lambda: c.session.state == CallState.SPEAKING)
        await asyncio.sleep(0.05)
        c.say("hayır bekleyin")
        await c.until(lambda: c.pbx.actions)
        assert c.pbx.actions[0]["type"] == "operator"
    assert c.stats.outcome == "operator"
