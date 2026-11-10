"""Diyalog senaryo setleri kural modunda (LLM'siz) CI'da koşar: bir değişiklik bir setteki davranışı bozarsa
birleştirmeden önce görülür. Önceden bu setler yalnız elle (tests/eval/diyalog.py --kural) ölçülüyordu;
gerilemeler ancak sonraki ölçümde fark ediliyordu (09.11.2026).

Bilinen başarısızlıklar listelidir (kural modunda çözülemeyen ya da karar bekleyen senaryolar). Liste dışındaki her
başarısızlık ve her izinsiz aktarma testi kırar; listedekinden biri düzelirse listeden çıkarılır.
"""
import asyncio
import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("diyalog_eval", ROOT / "tests/eval/diyalog.py")
assert _spec and _spec.loader
diyalog = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(diyalog)

SETS = [
    # (set, rehber, kural modunda bilinen başarısızlıklar)
    # D122 (kendini tanıtan arayanın adı aday sayılıyor), D124 / D125 (STT'nin bozduğu adda teyit gelmiyor): sabahki
    # sürümde (9c552ab) de CI'da başarısız; Windows ölçümünde geçiyorlardı (ortam farkı araştırılacak)
    ("diyalog_seti.yaml", "", {"D122", "D124", "D125"}),
    ("diyalog_seti_kamu.yaml", "tests/data/kamu_rehber.json", {"K19"}),        # K19: konu adımı (MCP) gerekir
    ("diyalog_seti_makam.yaml", "tests/data/makam_rehber.json", set()),
    ("diyalog_seti_demo.yaml", "data/demo_rehber.json", set()),
    ("diyalog_seti_sirket.yaml", "data/sirket_rehber.json", set()),
    # S18 ("vali yardımcısı kemal bey" çok aday), S28, S35 (karar bekliyor): birim yolu olmayan rehberde
    ("diyalog_seti_sentetik.yaml", "data/sentetik_kurum_rehber.json", {"S06", "S18", "S28", "S35"}),
]


@pytest.mark.parametrize("name, rehber, known", SETS, ids=[s[0] for s in SETS])
def test_set_kural_modunda(name, rehber, known, monkeypatch):
    for var in ("DIRECTORY_PATH", "ABBREVIATIONS_PATH", "MCP_STEP", "LLM_TOOLS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(ROOT)
    scenarios = yaml.safe_load((ROOT / "tests/data" / name).read_text(encoding="utf-8"))
    out = asyncio.run(diyalog.run_model("kural", scenarios, 1, None, rules=True,
                                        rehber=str(ROOT / rehber) if rehber else ""))
    failed = set(out["basarisizlar"])
    detail = {r["id"]: (r["cevaplar"], r["eylemler"]) for r in out["ayrinti"] if r["id"] in failed - known}
    for sid, (replies, actions) in detail.items():            # pytest uzun ayrıntıyı kısaltır: tamamı çıktıda
        print(f"{sid}: {replies} {actions}")
    assert out["izinsiz_aktarma"] == 0, detail
    assert failed <= known, detail
