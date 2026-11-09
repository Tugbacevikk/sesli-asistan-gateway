"""Kurumun STT düzeltmeleri (ADR-033) ve ad olmayan sözcükleri."""
import json

from sesli_asistan.gateway.abbreviations import load_abbreviations, load_stt_corrections
from sesli_asistan.gateway.core.corrections import SttCorrections
from sesli_asistan.gateway.directory_import import looks_like_person


def test_duzeltme_ve_ek_korunur():
    fix = SttCorrections({"masaj bakanlığı": "asayiş başkanlığı", "ama tepe": "Anıttepe", "yandarma": "jandarma"})
    assert fix.apply("Masaj Bakanlığına bağlar mısınız") == "asayiş başkanlığına bağlar mısınız"
    assert fix.apply("ama tepe kampüsü") == "Anıttepe kampüsü"
    assert fix.apply("yandarmaya") == "jandarmaya"
    assert fix.apply("ama ben muhasebeyi arıyorum") == "ama ben muhasebeyi arıyorum"   # yalnız tam kural
    assert SttCorrections().apply("masaj bakanlığı") == "masaj bakanlığı"             # ürün kodunda kural yok
    assert not SttCorrections() and fix


def test_kurum_dosyasindan(tmp_path):
    f = tmp_path / "kurum.json"
    f.write_text(json.dumps({"kisaltmalar": {}, "stt_duzeltmeleri": {"taş bakanlığı": "asayiş başkanlığı"},
                             "ad_olmayan_sozcukler": ["Yeşiltepe", "Kampüs"]}), encoding="utf-8")
    assert load_stt_corrections(str(f)).apply("taş bakanlığı") == "asayiş başkanlığı"
    assert not load_stt_corrections("")
    abbr = load_abbreviations(str(f))
    assert looks_like_person("Yeşiltepe Kampüs")                      # genel listede yok: kişi sanılır
    assert not looks_like_person("Yeşiltepe Kampüs", abbr.not_names)  # kurumun listesiyle makam hattı
