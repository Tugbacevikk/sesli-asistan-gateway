"""Devre kesici (tasarim.md 9.2)."""
import asyncio

import pytest

from sesli_asistan.gateway.core.ports import Audio, LlmError, LlmMessage, SttError, TtsError
from sesli_asistan.gateway.core.resilience import BreakerLlm, BreakerStt, BreakerTts, CircuitBreaker


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_acilir_soguma_yari_acik_kapanir():
    c = Clock()
    b = CircuitBreaker("llm", failures=3, reset_s=30, clock=c)
    b.failure()
    b.failure()
    assert b.state == "closed" and b.allow()
    b.failure()
    assert b.state == "open" and not b.allow() and b.trips == 1
    c.t = 31
    assert b.state == "half_open" and b.allow()          # bir deneme
    b.failure()                                          # deneme başarısız → hemen yeniden açık
    assert b.state == "open" and b.trips == 2
    c.t = 62
    b.success()
    assert b.state == "closed" and b.failures == 0


def test_basari_sayaci_sifirlar():
    b = CircuitBreaker("x", failures=3)
    b.failure()
    b.failure()
    b.success()
    b.failure()
    b.failure()
    assert b.state == "closed"


async def test_llm_devre_acikken_beklemeden_hata():
    calls = []

    class Down:
        async def chat(self, m, t):
            calls.append(1)
            raise LlmError("bağlantı yok")

    llm = BreakerLlm(Down(), CircuitBreaker("llm", failures=2, reset_s=60))
    for _ in range(2):
        with pytest.raises(LlmError):
            await llm.chat([], [])
    with pytest.raises(LlmError, match="devre açık"):
        await llm.chat([], [])
    assert len(calls) == 2                                # üçüncüde servis hiç çağrılmadı


async def test_llm_zaman_asimi_hata_sayilir():
    class Slow:
        async def chat(self, m, t):
            await asyncio.sleep(1)
            return LlmMessage("geç")

    b = CircuitBreaker("llm", failures=1)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(BreakerLlm(Slow(), b).chat([], []), 0.05)
    assert b.state == "open"


async def test_tts_ve_stt():
    class TtsDown:
        async def synthesize(self, text):
            raise TtsError("yok")

    tts = BreakerTts(TtsDown(), CircuitBreaker("tts", failures=1))
    with pytest.raises(TtsError):
        await tts.synthesize("a")
    with pytest.raises(TtsError, match="devre açık"):
        await tts.synthesize("a")

    class TtsOk:
        async def synthesize(self, text):
            await asyncio.sleep(1)
            return Audio(None, 8000)

    b = CircuitBreaker("tts", failures=1)
    t = asyncio.create_task(BreakerTts(TtsOk(), b).synthesize("a"))
    await asyncio.sleep(0.01)
    t.cancel()                                            # söze girme: hata sayılmaz
    with pytest.raises(asyncio.CancelledError):
        await t
    assert b.state == "closed"

    class SttDown:
        async def open(self, *a):
            raise SttError("yok")

    stt = BreakerStt(SttDown(), CircuitBreaker("stt", failures=1))
    with pytest.raises(SttError):
        await stt.open("c", "pcma", [])
    with pytest.raises(SttError, match="devre açık"):
        await stt.open("c", "pcma", [])


async def test_devre_aciksa_yeni_cagri_alinmaz():
    from pathlib import Path

    from sesli_asistan.gateway.composition import GatewayRuntime
    from sesli_asistan.gateway.config import GatewaySettings
    from tests.unit.test_ui import Llm, Stt, Tts

    root = Path(__file__).resolve().parents[2]
    rt = GatewayRuntime(GatewaySettings(_env_file=None, directory_path=str(root / "data/rehber_ornek.json")),
                        llm=Llm(), stt=Stt(), tts=Tts())
    await rt.start()
    try:
        assert rt.ready
        for _ in range(3):
            rt.breakers["llm"].failure()
        # LLM zorunlu değil (ADR-020): çağrı alınır, diyalog kural tabanlı
        h = rt.health()
        assert rt.ready and h["components"]["llm"] == "devre_acik" and h["mode"] == "kural"
        for _ in range(3):
            rt.breakers["tts"].failure()
        assert not rt.ready and rt.health()["status"] == "degraded"     # TTS zorunlu
    finally:
        await rt.stop()
