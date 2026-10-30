"""Sürüm yönetimi (tasarim.md 14.9): CHANGELOG ↔ pyproject tutarlılığı, sürüm hazırlama."""
import datetime as dt
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "kurulum"))
import surum  # noqa: E402


def test_depodaki_changelog_gecerli():
    sec = surum.sections((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
    v = surum.pyproject_version()
    assert v in sec and sec[v].strip()                     # mevcut sürümün notları var
    assert surum.YAYIMLANMAMIS in sec                      # yeni değişiklikler için başlık duruyor
    assert surum.check_tag(f"v{v}") == v
    assert "[Yayımlanmamış]" not in surum.notes(v)         # bağlantı tanımları notlara girmez


def kopya(tmp_path):
    for f in ("CHANGELOG.md", "pyproject.toml"):
        shutil.copy(ROOT / f, tmp_path / f)
    return tmp_path


def test_hazirla_surumu_ve_basligi_gunceller(tmp_path):
    kok = kopya(tmp_path)
    old = surum.pyproject_version(kok)
    new = ".".join([*old.split(".")[:1], str(int(old.split(".")[1]) + 1), "0"])
    unreleased = surum.sections((kok / "CHANGELOG.md").read_text())[surum.YAYIMLANMAMIS]
    surum.prepare(new, kok, today=dt.date(2026, 11, 2))
    assert surum.pyproject_version(kok) == new
    text = (kok / "CHANGELOG.md").read_text()
    sec = surum.sections(text)
    assert sec[surum.YAYIMLANMAMIS] == "" and sec[new] == unreleased
    assert f"## [{new}] — 2026-11-02" in text and f"compare/v{new}...HEAD" in text
    assert f"compare/v{old}...v{new}" in text
    assert surum.check_tag(f"v{new}", kok) == new
    with pytest.raises(SystemExit, match="boş"):           # yeni değişiklik yokken ikinci sürüm çıkmaz
        surum.prepare("9.0.0", kok)


@pytest.mark.parametrize("tag,msg", [("0.2", "anlamsal"), ("v9.9.9", "pyproject.toml sürümü"),
                                     ("v1.2.3-rc1", "anlamsal")])
def test_tutarsiz_etiket_reddedilir(tag, msg):
    with pytest.raises(SystemExit, match=msg):
        surum.check_tag(tag)


def test_geri_surum_hazirlanamaz(tmp_path):
    kok = kopya(tmp_path)
    with pytest.raises(SystemExit, match="büyük olmalı"):
        surum.prepare("0.0.1", kok)


def test_ci_surum_isi_denetimleri_cagirir():
    wf = (ROOT / ".github/workflows/surum.yml").read_text()
    assert "uses: ./.github/workflows/ci.yml" in wf and "surum.py denetle" in wf and "needs: denetim" in wf
    assert "workflow_call" in (ROOT / ".github/workflows/ci.yml").read_text()
