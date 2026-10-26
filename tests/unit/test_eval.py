"""Değerlendirme betiğinin puanlaması ve diyalog setinin geçerliliği."""
from pathlib import Path

import yaml

from sesli_asistan.gateway.core.models import OperatorAction, TransferAction
from tests.eval.diyalog import evaluate

ROOT = Path(__file__).resolve().parents[2]


def T(ext):
    return TransferAction(extension=ext, contact_id="x")


def test_puanlama():
    assert evaluate({"eylem": "transfer", "dahili": "214"}, [T("214")]) == (True, [])
    assert evaluate({"eylem": "transfer", "dahili": "214"}, [T("231")]) == (False, ["231"])
    assert evaluate({"eylem": "operator"}, [OperatorAction("x")]) == (True, [])
    assert evaluate({"eylem": "yok", "yasak_dahili": ["100"]}, [T("100")]) == (False, ["100"])
    assert evaluate({"eylem": "yok"}, []) == (True, [])


def test_set_gecerli_ve_dahililer_rehberde():
    import json

    rehber = json.loads((ROOT / "data/rehber_ornek.json").read_text(encoding="utf-8"))["contacts"]
    aktarilabilir = {c["extension"] for c in rehber if c.get("transferable", True) and not c.get("deleted")}
    sc = yaml.safe_load((ROOT / "tests/data/diyalog_seti.yaml").read_text(encoding="utf-8"))
    ids = [s["id"] for s in sc]
    assert len(ids) == len(set(ids)) >= 30
    for s in sc:
        b = s["beklenen"]
        assert b["eylem"] in {"transfer", "operator", "yok"}, s["id"]
        if b["eylem"] == "transfer":
            assert b["dahili"] in aktarilabilir, s["id"]
