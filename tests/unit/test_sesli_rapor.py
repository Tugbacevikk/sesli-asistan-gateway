"""Sesli deneme raporu (tools/deneme/sesli_rapor.py): çağrı kayıtlarını okuma listesiyle eşler."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("sesli_rapor", ROOT / "tools/deneme/sesli_rapor.py")
assert _spec and _spec.loader
rapor = importlib.util.module_from_spec(_spec)
sys.modules["sesli_rapor"] = rapor          # dataclass modülü bulabilsin
_spec.loader.exec_module(rapor)


def _kayit(gun: Path, saat: str, turlar: list[tuple[str, str, list[str]]], outcome: str = "") -> None:
    ozet = {"turns": [{"heard": h, "reply": r, "actions": a} for h, r, a in turlar], "outcome": outcome,
            "channel": "web", "started": f"2026-11-25T{saat}:00"}
    (gun / f"{saat.replace(':', '')}00_c{saat[-2:]}.log").write_text(
        "satır\n--- özet ---\n" + json.dumps(ozet, ensure_ascii=False), encoding="utf-8")


def test_rapor_eslestirir_ve_degerlendirir(tmp_path, capsys):
    gun = tmp_path / "2026-11-25"
    gun.mkdir()
    _kayit(gun, "14:00", [("mehmet demire bağlar mısınız", "Aktarıyorum", ["transfer:20011"])], "transferred")
    # S02 iki kez denendi: sonuncusu sayılır
    _kayit(gun, "14:01", [("elif kurt hanımla", "Bulamadım", [])])
    _kayit(gun, "14:02", [("elif kurt hanımla görüşmek istiyorum", "Aktarıyorum", ["transfer:51021"])])
    _kayit(gun, "14:03", [("murat şenle görüşebilir miyim", "Aktarıyorum", ["transfer:40212"])])   # yanlış kişi
    assert rapor.main(["rapor", "--klasor", str(tmp_path), "--gun", "2026-11-25",
                       "--cikti", str(tmp_path / "r.md")]) == 0
    satirlar = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert [(r["id"], r["sonuc"], r["deneme"]) for r in satirlar] == [
        ("S01", "DOĞRU", 1), ("S02", "DOĞRU", 2), ("S03", "YANLIŞ YER", 1)]
    assert "YANLIŞ YER" in capsys.readouterr().out


def test_wer():
    assert rapor.wer("murat şen ile", "murat şen ile") == 0
    assert rapor.wer("murat şen ile", "murat şenle") == 2 / 3
