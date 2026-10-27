"""Yük testi aracı (tools/yuk/yuk.py): gerçek HTTP + UDP/RTP ile süreç içi Gateway'e eşzamanlı çağrılar.

Gateway → gRPC STT (sahte Whisper, enerji VAD) → sahte LLM (her çağrıda Ayşe araması → otomatik aktarma) → sahte TTS.
"""
import asyncio
import socket
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
import uvicorn
from test_e2e_call import Tts, Whisper

from sesli_asistan.gateway.adapters.stt.grpc_client import GrpcStt
from sesli_asistan.gateway.api.app import create_app
from sesli_asistan.gateway.composition import GatewayRuntime
from sesli_asistan.gateway.config import GatewaySettings
from sesli_asistan.gateway.core.ports import LlmMessage, ToolCall
from sesli_asistan.stt.config import SttSettings
from sesli_asistan.stt.server import start_server

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "yuk"))
from yuk import LoadConfig, pct, run_load  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Llm:
    async def chat(self, messages, tools):
        if any(m.get("role") == "tool" for m in messages):
            return LlmMessage("Aktarıyorum.")
        return LlmMessage("", [ToolCall("c", "InContacts", {"Name": "Ayşe", "Department": "muhasebe"})])


@pytest.fixture
async def gateway(tmp_path):
    made = []

    async def make(max_calls=10):
        srv, _, stt_port = await start_server(SttSettings(vad="energy"), Whisper("muhasebeden ayşe hanım"),
                                              bind="127.0.0.1:0")
        pbx_port, api_port = free_port(), free_port()
        s = GatewaySettings(_env_file=None, directory_path=str(ROOT / "data/rehber_ornek.json"), rtp_host="127.0.0.1",
                            rtp_port_min=32000, rtp_port_max=32099, pbx_allowed_ips="127.0.0.1",
                            pbx_base_url=f"http://127.0.0.1:{pbx_port}", max_concurrent_calls=max_calls,
                            thinking_sound=False)
        rt = GatewayRuntime(s, llm=Llm(), stt=GrpcStt(f"127.0.0.1:{stt_port}"), tts=Tts())
        await rt.start()
        app = create_app(rt.manager, rt.health, allowed_ips={"127.0.0.1"})
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=api_port, log_level="warning"))
        task = asyncio.create_task(server.serve())
        while not server.started:
            await asyncio.sleep(0.02)
        made.append((srv, rt, server, task))
        wav = tmp_path / "arayan.wav"
        t = np.arange(8000) / 8000
        sf.write(wav, np.concatenate([np.zeros(2400), 0.3 * np.sin(2 * np.pi * 300 * t), np.zeros(800)]), 8000)
        return rt, f"http://127.0.0.1:{api_port}", pbx_port, str(wav)
    yield make
    for srv, rt, server, task in made:
        server.should_exit = True
        await task
        await rt.stop()
        await srv.stop(0)


async def test_eszamanli_cagrilar_olculur(gateway):
    rt, url, pbx_port, wav = await gateway()
    rep = await run_load(LoadConfig([wav], url, pbx_port=pbx_port, calls=6, concurrency=3, interval_s=0.1,
                                    wait_s=5), progress=lambda _: None)
    assert rep["sonuc"]["basarili"] == 6 and rep["sonuc"]["hata"] == 0 and rep["sonuc"]["reddedilen"] == 0
    assert rep["sonuc"]["aktarma"] == 6 and rep["sonuc"]["cevapsiz_tur"] == 0
    assert 2 <= rep["sonuc"]["en_yuksek_eszamanli"] <= 3
    lat = rep["tur_gecikmesi_sn"]
    assert lat["adet"] == 6 and 0 < lat["p50"] <= lat["p95"] <= lat["en_cok"] < 3
    assert rep["karsilama_sn"]["p50"] is not None
    await asyncio.sleep(0.3)
    assert len(rt.manager.results) == 6 and all(st.outcome == "transferred" for _, st in rt.manager.results)


async def test_kapasite_dolunca_reddedilen_sayilir(gateway):
    _, url, pbx_port, wav = await gateway(max_calls=1)
    rep = await run_load(LoadConfig([wav], url, pbx_port=pbx_port, calls=2, concurrency=2, interval_s=0,
                                    wait_s=5), progress=lambda _: None)
    assert rep["sonuc"]["basarili"] == 1 and rep["sonuc"]["reddedilen"] == 1 and rep["sonuc"]["hata"] == 0


def test_yuzdelik():
    assert pct([], 95) is None and pct([1.0, 2.0, 3.0, 4.0], 50) == 2.5
