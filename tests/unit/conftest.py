import pytest


@pytest.fixture(autouse=True)
def _gecici_cagri_gecmisi(tmp_path, monkeypatch):
    """Testler çalışma klasörüne veritabanı yazmasın."""
    monkeypatch.setenv("CALL_HISTORY_DB", str(tmp_path / "cagri_gecmisi.db"))
    monkeypatch.setenv("LOCAL_SETTINGS_FILE", str(tmp_path / "yerel_ayarlar.json"))
