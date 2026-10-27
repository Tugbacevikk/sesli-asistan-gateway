"""Gateway ayarları: metinler ve doğrulama (tasarim.md 13)."""
import pytest
from pydantic import ValidationError

from sesli_asistan.gateway.composition import build_agent, build_directory
from sesli_asistan.gateway.config import GatewaySettings
from sesli_asistan.gateway.core.dialog import DialogTexts


def settings(**kw):
    return GatewaySettings(_env_file=None, **kw)


def test_metinler_ayardan_gelir_bos_olanlar_varsayilan():
    s = settings(organization_name="Atak", greeting_text="{kurum_e} hoş geldiniz, kimi aramıştınız?",
                 to_operator_text="Operatörümüze aktarıyorum.")
    t = s.dialog_texts()
    assert t.to_operator == "Operatörümüze aktarıyorum." and t.error == DialogTexts().error
    agent = build_agent(s, build_directory(s), llm=object())
    assert agent.new_session("x").greeting().text == "Atak'a hoş geldiniz, kimi aramıştınız?"
    assert agent.fixed_prompts()["to_operator"] == "Operatörümüze aktarıyorum."   # açılışta seslendirilir


def test_metin_ortam_degiskeninden(monkeypatch):
    monkeypatch.setenv("NO_INPUT_TEXT", "Hatta mısınız?")
    assert GatewaySettings(_env_file=None).dialog_texts().no_input == "Hatta mısınız?"


@pytest.mark.parametrize("kw", [
    {"greeting_text": "{firma} hoş geldiniz"},                  # bilinmeyen yer tutucu
    {"transferring_text": "Aktarıyorum."},                      # kime aktarıldığı söylenmiyor
    {"transferring_department_text": "Birime aktarıyorum."},
    {"rtp_port_min": 20000, "rtp_port_max": 10000},
    {"rtp_port_min": 10000, "rtp_port_max": 10004, "max_concurrent_calls": 30},
])
def test_gecersiz_ayar_servisi_baslatmaz(kw):
    with pytest.raises(ValidationError):
        settings(**kw)
