"""Sesli denemeden kelime düzeltmesi önerisi (tools/deneme/stt_duzeltme.py)."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("stt_duzeltme", ROOT / "tools/deneme/stt_duzeltme.py")
assert _spec and _spec.loader
sd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sd)


def test_rehber_sozcugunun_bozulmasi_oneri_olur(tmp_path):
    rapor = tmp_path / "r.json"
    rapor.write_text(json.dumps([
        {"soylenen": "asayiş şube müdürlüğüne bağlayın", "duyulan": "masaj şube müdürlüğüne bağlayın"},
        {"soylenen": "valilik hukuk işlerine bağlar mısınız / evet",
         "duyulan": "valilik hukuk dişlerine bağlar mısınız / evet"},
        {"soylenen": "dereköy kaymakamlığına", "duyulan": "dere köy kaymakamlığına"},
        {"soylenen": "görüşmek istiyorum", "duyulan": "görüşmek isterim"},              # rehberle ilgisiz
        {"soylenen": "jandarmaya bağlayın", "duyulan": "jandarmadan bağlayın"},          # yalnız ek farkı
        {"soylenen": "operatöre bağlayın", "duyulan": "evet bağlayın"},                  # cevap sözcüğü
    ], ensure_ascii=False), encoding="utf-8")
    cikti = tmp_path / "o.json"
    assert sd.main([str(rapor), "--rehber", str(ROOT / "data/demo_rehber.json"), "--cikti", str(cikti)]) == 0
    tablo = json.loads(cikti.read_text(encoding="utf-8"))["stt_duzeltmeleri"]
    assert tablo == {"masaj": "asayiş", "dişler": "işler", "dere köy": "dereköy"}


def test_ek_korunur_ve_gercek_ad_duzeltilmez():
    kelime, gercek = sd.rehber_sozleri(ROOT / "data/demo_rehber.json")
    assert sd.ortak_ek(["masaj", "bakanlığına"], ["asayiş", "başkanlığına"]) == (["masaj", "bakanlığı"],
                                                                                 ["asayiş", "başkanlığı"])
    from sesli_asistan.gateway.core.corrections import SttCorrections
    kural = SttCorrections({"masaj bakanlığı": "asayiş başkanlığı"})
    assert kural.apply("masaj bakanlığına bağlayın") == "asayiş başkanlığına bağlayın"
    satir = [{"soylenen": "tolga koç", "duyulan": "kemal yılmaz"}]      # duyulan rehberde gerçek bir ad
    assert ("kemal yılmaz", "tolga koç") not in sd.oneriler(satir, kelime, gercek)
