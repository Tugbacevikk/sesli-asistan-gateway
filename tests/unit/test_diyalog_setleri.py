"""Diyalog senaryo setleri kural modunda (LLM'siz) CI'da koşar: bir değişiklik bir setteki davranışı bozarsa
birleştirmeden önce görülür. Önceden bu setler yalnız elle (tests/eval/diyalog.py --kural) ölçülüyordu;
gerilemeler ancak sonraki ölçümde fark ediliyordu (09.11.2026).

Bilinen başarısızlıklar listelidir (kural modunda çözülemeyen ya da karar bekleyen senaryolar). Liste dışındaki her
başarısızlık ve her izinsiz aktarma testi kırar; listedekinden biri düzelirse listeden çıkarılır.
"""
import asyncio
import importlib.util
import logging
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
    ("diyalog_seti.yaml", "", set()),
    ("diyalog_seti_kamu.yaml", "tests/data/kamu_rehber.json", {"K19"}),        # K19: konu adımı (MCP) gerekir
    ("diyalog_seti_makam.yaml", "tests/data/makam_rehber.json", set()),
    ("diyalog_seti_demo.yaml", "data/demo_rehber.json", set()),
    ("diyalog_seti_sirket.yaml", "data/sirket_rehber.json", set()),
    ("diyalog_seti_sentetik.yaml", "data/sentetik_kurum_rehber.json", set()),
]


@pytest.mark.parametrize("name, rehber, known", SETS, ids=[s[0] for s in SETS])
def test_set_kural_modunda(name, rehber, known, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="sesli_asistan.gateway.core")
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
        for rec in caplog.records:                             # o senaryonun diyalog kaydı (neden bu cevap?)
            if f"[{sid}]" in rec.getMessage():
                print(f"    {rec.getMessage()[:300]}")
    assert out["izinsiz_aktarma"] == 0, detail
    assert failed <= known, detail
