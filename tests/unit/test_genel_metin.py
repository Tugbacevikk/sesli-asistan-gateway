"""Dil modeli derlemine genel cümle (tools/stt/dil_modeli/genel_metin.py, derlem.py --genel)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/stt/dil_modeli"))
import derlem  # noqa: E402
import genel_metin  # noqa: E402


def test_stt_bicimine_cevirir():
    n = genel_metin.normalize
    assert n("Ancak Kızıl Ordu'dan 800.000'den fazla asker.") == "ancak kızıl ordu'dan sekiz yüz binden fazla asker"
    assert n("Otobüsler 06:30 ile 07:30 arası kalkar.") == "otobüsler altı otuz ile yedi otuz arası kalkar"
    assert n("Evet.") == ""                                     # çok kısa: santral derleminde zaten var


def test_derleme_genel_pay_katilir(tmp_path):
    genel = tmp_path / "genel.txt"
    genel.write_text("bu uzun bir genel türkçe cümle örneğidir\nikinci genel cümle burada duruyor\n", encoding="utf-8")
    out = tmp_path / "d.txt"
    assert derlem.main(["--rehber", str(ROOT / "data/demo_rehber.json"), "--set",
                        str(ROOT / "tests/data/diyalog_seti_demo.yaml"), "--adet", "300", "--genel", str(genel),
                        "--genel-oran", "0.25", "--cikti", str(out)]) == 0
    lines = out.read_text(encoding="utf-8").splitlines()
    g = sum(x in ("bu uzun bir genel türkçe cümle örneğidir", "ikinci genel cümle burada duruyor") for x in lines)
    assert len(lines) == 400 and g == 100
