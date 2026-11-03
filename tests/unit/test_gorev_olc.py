"""tools/stt/gorev_olc.py: STT metni + güveni diyalogdan geçirilip sonuç türlerine ayrılır."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "stt"))
import gorev_olc as g  # noqa: E402


def test_siniflama():
    assert g.classify("transfer:214", "transfer:214") == "doğru"
    assert g.classify("teyit:214", "transfer:214") == "teyitle doğru"
    assert g.classify("transfer:215", "transfer:214") == "YANLIŞ EYLEM"
    assert g.classify("operator", "kaçırma") == "YANLIŞ EYLEM"
    assert g.classify("teyit:215", "transfer:214") == "yanlış teyit"
    assert g.classify("kaçırma", "transfer:214") == "kaçırma"


def test_guvenle_teyit_ve_baglam(tmp_path, capsys):
    data = {"satirlar": [
        {"dosya": "k01.wav", "dogru": "Ayşe Yılmaz ile görüşmek istiyorum",
         "A": "ayşe yılmaz ile görüşmek istiyorum", "A#guven": 0.6,
         "B": "burak çelik ile görüşmek istiyorum", "B#guven": 0.95},
        {"dosya": "k18.wav", "dogru": "evet", "A": "evet", "A#guven": 0.4, "B": "ayşe yılmaz", "B#guven": 0.9},
        {"dosya": "k20.wav", "dogru": "hiçbiri", "A": "hiçbiri", "A#guven": 0.9, "B": "", "B#guven": 0.0},
    ], "ozet": {"A": {"RTF": 0.1}, "B": {"RTF": 0.2}}}
    f = tmp_path / "s.json"
    f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert g.main([str(f), "--rehber", str(ROOT / "data" / "rehber_ornek.json")]) == 0
    out = capsys.readouterr().out
    assert "| A | 2 | 1 | 0 | 0 | 0 | 0.1 |" in out           # evet (teyit bağlamı) + hiçbiri doğru, ad teyitle
    # B: "evet" yerine "ayşe yılmaz" teyitteki kişiye gider (doğru); boş cevap "hiçbiri" gibi bulamadım (doğru);
    # ama ad yanlış kişiye aktarır
    assert "| B | 2 | 0 | 0 | 0 | 1 | 0.2 |" in out
