"""Her konuşmanın log dosyası (gateway/call_logs.py, CALL_LOG_DIR)."""
import asyncio
import json
import logging
from datetime import date, timedelta

from sesli_asistan.gateway.call_logs import CallLogRecorder, attach, detach
from sesli_asistan.gateway.core.call import CallStats, TurnRecord
from sesli_asistan.libs.obs import CURRENT_CALL

log = logging.getLogger("sesli_asistan.gateway.core.call")


def test_cagrinin_satirlari_ve_ozeti_dosyaya_yazilir(tmp_path):
    rec = attach(str(tmp_path))
    try:
        log.info("[c-1] tur: 'muhasebe' → 'Sizi Muhasebe birimine aktarıyorum.'")
        log.info("[c-2] başka çağrı")

        async def in_session():                      # ön eksiz satır: oturum görevinin kimliği
            CURRENT_CALL.set("c-1")
            logging.getLogger("sesli_asistan.gateway.core.tools").info('araç InContacts({"Department": "x"})')
        asyncio.run(in_session())
        logging.getLogger("sesli_asistan.gateway").info("çağrısız satır")
        stats = CallStats(turns=[TurnRecord("muhasebe", "Sizi …", ["transfer:1400"], 0.01, 0.4)], outcome="transferred")
        path = rec.flush_call("c-1", stats)
    finally:
        detach(rec)
    text = path.read_text(encoding="utf-8")
    assert path.parent.name == date.today().isoformat() and path.name.endswith("_c-1.log")
    assert "tur: 'muhasebe'" in text and "araç InContacts" in text
    assert "başka çağrı" not in text and "çağrısız satır" not in text
    summary = json.loads(text.split("--- özet ---\n", 1)[1])
    assert summary["outcome"] == "transferred" and summary["turns"][0]["actions"] == ["transfer:1400"]
    assert rec.flush_call("c-1") is None                       # bellek boşaldı


def test_eski_gunler_silinir_bos_klasor_kapali(tmp_path):
    rec = CallLogRecorder(tmp_path)
    old = tmp_path / (date.today() - timedelta(days=40)).isoformat()
    new = tmp_path / date.today().isoformat()
    other = tmp_path / "notlar"
    for d in (old, new, other):
        d.mkdir()
    assert rec.purge(30) == 1 and not old.exists() and new.exists() and other.exists()
    assert attach("") is None and attach("  ") is None


def test_gateway_konusma_bitince_dosya_yazar(tmp_path, monkeypatch):
    from sesli_asistan.gateway.composition import GatewayRuntime
    from sesli_asistan.gateway.config import GatewaySettings

    monkeypatch.setenv("CALL_LOG_DIR", str(tmp_path / "kayit"))
    rt = GatewayRuntime(GatewaySettings(_env_file=None), llm=object(), stt=object(), tts=object())
    try:
        log.info("[web-1] çağrı açıldı")
        asyncio.run(rt._record("web-1", CallStats(outcome="hangup")))
    finally:
        detach(rt.call_logs)
        rt.history.close()
    files = list((tmp_path / "kayit").rglob("*_web-1.log"))
    assert len(files) == 1 and "çağrı açıldı" in files[0].read_text(encoding="utf-8")
