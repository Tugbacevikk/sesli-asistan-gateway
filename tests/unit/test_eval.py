"""Değerlendirme betiğinin puanlaması ve diyalog setinin geçerliliği."""
from pathlib import Path

import yaml

from sesli_asistan.gateway.core.models import OperatorAction, TransferAction
from tests.eval.diyalog import evaluate

ROOT = Path(__file__).resolve().parents[2]


def T(ext):
    return TransferAction(extension=ext, contact_id="x")


def U(*acts):
    return ("arayan", list(acts))


def S(status):
    return (f"santral:{status}", [])


ALLOWED = {"214", "210", "231", "260"}


def test_puanlama():
    assert evaluate({"eylem": "transfer", "dahili": "214"}, [U(T("214"))], ALLOWED) == (True, [], [])
    assert evaluate({"eylem": "transfer", "dahili": "214"}, [U(T("231"))], ALLOWED) == (False, [], ["231"])
    assert evaluate({"eylem": "operator"}, [U(OperatorAction("x"))], ALLOWED) == (True, [], [])
    assert evaluate({"eylem": "yok", "yasak_dahili": ["100"]}, [U(T("100"))], ALLOWED) == (False, ["100"], [])
    assert evaluate({"eylem": "yok"}, [U()], ALLOWED) == (True, [], [])
    assert evaluate({"eylem": "aktarma_yok"}, [U(OperatorAction("x"))], ALLOWED) == (True, [], [])
    assert evaluate({"eylem": "aktarma_yok"}, [U(T("999"))], ALLOWED) == (False, ["999"], [])  # rehberde yok


def test_santralin_reddettigi_aktarma_denenmis_sayilir():
    # D24: 214 denendi, meşgul; sonra birime (210) aktarıldı → başarılı, izinsiz/yanlış yok
    steps = [U(T("214")), S("busy"), U(T("210"))]
    assert evaluate({"eylem": "transfer", "dahili": "210"}, steps, ALLOWED) == (True, [], [])
    # D25: 260 denendi, cevapsız; arayan operatör istedi
    steps = [U(T("260")), S("no_answer"), U(OperatorAction("x"))]
    assert evaluate({"eylem": "operator"}, steps, ALLOWED) == (True, [], [])
    # santral olayından sonra tekrar aynı kişiye aktarma: yanlış
    steps = [U(T("260")), S("no_answer"), U(T("260")), U(OperatorAction("x"))]
    assert evaluate({"eylem": "operator"}, steps, ALLOWED) == (False, [], ["260"])


def test_set_gecerli_ve_dahililer_rehberde():
    import json

    rehber = json.loads((ROOT / "data/rehber_ornek.json").read_text(encoding="utf-8"))["contacts"]
    aktarilabilir = {c["extension"] for c in rehber if c.get("transferable", True) and not c.get("deleted")}
    sc = yaml.safe_load((ROOT / "tests/data/diyalog_seti.yaml").read_text(encoding="utf-8"))
    ids = [s["id"] for s in sc]
    assert len(ids) == len(set(ids)) >= 30
    for s in sc:
        b = s["beklenen"]
        assert b["eylem"] in {"transfer", "operator", "yok", "aktarma_yok"}, s["id"]
        if b["eylem"] == "transfer":
            assert b["dahili"] in aktarilabilir, s["id"]
