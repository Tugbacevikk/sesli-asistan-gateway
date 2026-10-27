"""Alarm kuralları ve Grafana panosu (deploy/izleme/): andıkları metrikler gerçekten üretiliyor mu."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
IZ = ROOT / "deploy" / "izleme"
FUNCS = {"sum", "rate", "increase", "histogram_quantile", "by", "le", "and", "or", "max", "min", "avg", "on",
         "without", "ignoring", "unless", "count", "abs", "time"}


def produced() -> set[str]:
    """Kodun ürettiği metrik adları (prometheus_client tanımlarından ve toplayıcıdan)."""
    names = set()
    for f in ("src/sesli_asistan/gateway/metrics.py", "src/sesli_asistan/stt/service.py"):
        text = (ROOT / f).read_text(encoding="utf-8")
        for kind, name in re.findall(r"(Counter|Histogram|Gauge|CounterMetricFamily|GaugeMetricFamily)\(\s*\"(\w+)\"",
                                     text):
            if kind in ("Counter", "CounterMetricFamily"):
                names.add(name if name.endswith("_total") else f"{name}_total")
            elif kind == "Histogram":
                names |= {f"{name}_bucket", f"{name}_sum", f"{name}_count"}
            else:
                names.add(name)
    return names | {"up"}


def referenced(expr: str) -> set[str]:
    expr = re.sub(r"\{[^}]*\}", "", expr)
    expr = re.sub(r"\b(by|without|on|ignoring)\s*\([^)]*\)", "", expr)    # gruplama etiketleri metrik değil
    expr = re.sub(r"\[[^\]]*\]", "", expr)
    return {w for w in re.findall(r"[a-zA-Z_:][\w:]*", expr) if w not in FUNCS and not w.isdigit()}


def test_alarmlar_ve_pano_var_olan_metrikleri_kullanir():
    rules = yaml.safe_load((IZ / "alarmlar.yml").read_text(encoding="utf-8"))
    recorded = {r["record"] for g in rules["groups"] for r in g["rules"] if "record" in r}
    known = produced() | recorded
    exprs = [r["expr"] for g in rules["groups"] for r in g["rules"]]
    dash = json.loads((IZ / "grafana-pano.json").read_text(encoding="utf-8"))
    exprs += [t["expr"] for p in dash["panels"] for t in p["targets"]]
    unknown = {m for e in exprs for m in referenced(e)} - known
    assert not unknown, f"kodda olmayan metrik: {unknown}"
    assert {"SesliGatewayErisilemiyor", "SesliTurGecikmesiYuksek", "SesliDevreAcik", "SesliKapasiteDoluyor"} <= {
        r.get("alert") for g in rules["groups"] for r in g["rules"]}


@pytest.mark.skipif(shutil.which("promtool") is None, reason="promtool yok")
def test_promtool_kurallar_ve_birim_testleri():
    for args in (["check", "rules", "alarmlar.yml"], ["test", "rules", "alarmlar_test.yml"]):
        r = subprocess.run(["promtool", *args], cwd=IZ, capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
