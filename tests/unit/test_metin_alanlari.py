"""Koddan söylenen cümleler dört yerde tanımlı olmalı: DialogTexts (varsayılan), TEXT_FIELDS (ayardan değişebilenler),
GatewaySettings `<ad>_text` (ortam değişkeni) ve ayar ekranı etiketi. Biri unutulunca ayar ekranı KeyError veriyordu
(09.11.2026: FAX_LINE_TEXT etiketi unutuldu, 9 test kırıldı); bu test hangisinin eksik olduğunu söyler."""
from dataclasses import fields

from sesli_asistan.gateway.config import TEXT_FIELDS, GatewaySettings
from sesli_asistan.gateway.core.dialog import DialogTexts
from sesli_asistan.gateway.settings_service import TEXT_LABELS


def test_her_cumle_ayarda_ve_ekranda_tanimli():
    names = {f.name for f in fields(DialogTexts)}
    assert set(TEXT_FIELDS) <= names
    missing_setting = [n for n in TEXT_FIELDS if f"{n}_text" not in GatewaySettings.model_fields]
    missing_label = [n for n in TEXT_FIELDS if n not in TEXT_LABELS]
    assert not missing_setting, f"config.py GatewaySettings'e ekle: {[f'{n}_text' for n in missing_setting]}"
    assert not missing_label, f"settings_service.TEXT_LABELS'a ekle: {missing_label}"
    assert not set(TEXT_LABELS) - set(TEXT_FIELDS), "TEXT_LABELS'ta artık olmayan cümle var"
