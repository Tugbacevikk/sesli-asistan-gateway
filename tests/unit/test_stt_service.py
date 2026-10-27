"""Aşama 4: gRPC STT servisi ve Gateway istemcisi (gerçek gRPC, sahte Whisper, enerji VAD)."""
import asyncio
import time

import grpc
import numpy as np
import pytest
from grpc_health.v1 import health_pb2, health_pb2_grpc

from sesli_asistan.contracts.stt.v1 import stt_pb2, stt_pb2_grpc
from sesli_asistan.gateway.adapters.stt.grpc_client import GrpcStt
from sesli_asistan.gateway.core.ports import SttError
from sesli_asistan.libs.audio import g711
from sesli_asistan.stt.config import SttSettings
from sesli_asistan.stt.engine import SttResult
from sesli_asistan.stt.server import start_server

RATE = 8000


class FakeEngine:
    def __init__(self, text="muhasebeden ayşe hanım", delay=0.0):
        self.text, self.delay, self.calls = text, delay, []

    def transcribe(self, samples, hotwords=None):
        self.calls.append((len(samples), hotwords))
        time.sleep(self.delay)
        return SttResult(self.text, 0.9)


def frames(*parts):
    """(saniye, ses var mı) parçalarından 20 ms PCMA kareleri."""
    x = []
    for sec, voiced in parts:
        n = int(sec * RATE)
        t = np.arange(n) / RATE
        x.append(0.3 * np.sin(2 * np.pi * 300 * t) if voiced else np.zeros(n))
    pcm = np.concatenate(x).astype(np.float32)
    payload = g711.encode(pcm, "pcma")
    return [payload[i:i + 160] for i in range(0, len(payload), 160)]


@pytest.fixture
async def server():
    async def make(engine=None, **kw):
        s = SttSettings(vad="energy", max_streams=kw.pop("max_streams", 4), timeout_s=kw.pop("timeout_s", 3.0),
                        debug_dir=kw.pop("debug_dir", ""))
        srv, health_svc, port = await start_server(s, engine or FakeEngine(), bind="127.0.0.1:0")
        made.append(srv)
        return f"127.0.0.1:{port}"
    made = []
    yield make
    for srv in made:
        await srv.stop(0)


async def run_stream(target, audio, hotwords=None, codec="pcma"):
    client = GrpcStt(target)
    st = await client.open("c-1", codec, hotwords)
    for f in audio:
        await st.send(f)
    await st.close()
    evs = [e async for e in st.events()]
    await client.aclose()
    return evs


async def test_konusma_baslangici_ve_final(server):
    eng = FakeEngine()
    target = await server(eng)
    evs = await run_stream(target, frames((0.5, False), (1.0, True), (1.0, False)), hotwords=["Ayşe Yılmaz"])
    assert [e.type for e in evs] == ["speech_started", "final"]
    assert evs[1].text == "muhasebeden ayşe hanım" and evs[1].confidence == pytest.approx(0.9)
    assert 100 <= evs[0].start_ms <= 500 and evs[1].end_ms > evs[1].start_ms
    n, hw = eng.calls[0]
    assert hw == ["Ayşe Yılmaz"] and 15000 < n < 30000   # ~1–1,6 sn @16 kHz (8→16 kHz çevrildi)


async def test_akis_sonunda_yarim_cumle_de_cozulur(server):
    evs = await run_stream(await server(), frames((0.3, False), (1.0, True)))   # sessizlik gelmeden bitti
    assert [e.type for e in evs] == ["speech_started", "final"]


async def test_bos_metin_taninamadi_olur(server):
    evs = await run_stream(await server(FakeEngine(text="")), frames((0.3, False), (1.0, True), (1.0, False)))
    assert [e.type for e in evs] == ["speech_started", "not_recognized"]
    assert evs[1].reason == "empty" and evs[1].text == "" and evs[1].end_ms > evs[1].start_ms


async def test_cozum_surerken_okuma_durmaz(server):
    # Whisper her cümlede 0,6 sn sürüyor; iki cümle arka arkaya geliyor. İkincinin SPEECH_STARTED'ı
    # birincinin FINAL'ını beklememeli (okuyucu çözücüden bağımsız).
    target = await server(FakeEngine(delay=0.6))
    audio = frames((0.3, False), (0.8, True), (0.9, False), (0.8, True), (0.9, False))
    client = GrpcStt(target)
    st = await client.open("c-2", "pcma")
    got = []

    async def reader():
        async for e in st.events():
            got.append(e.type)

    task = asyncio.create_task(reader())
    for f in audio:
        await st.send(f)
    await asyncio.sleep(0.3)
    await st.close()
    await task
    await client.aclose()
    assert got.count("final") == 2
    assert got.index("speech_started", 1) < got.index("final")   # 2. konuşma başlangıcı 1. FINAL'dan önce


async def test_zaman_asiminda_arayan_sessiz_kalmaz(server):
    # Antigravity STT ölçümü (CPU): çözüm 3 sn'yi aştı, FINAL atlandı ve Gateway'e hiçbir şey gitmedi
    evs = await run_stream(await server(FakeEngine(delay=0.5), timeout_s=0.1),
                           frames((0.3, False), (1.0, True), (1.0, False)))
    assert [e.type for e in evs] == ["speech_started", "not_recognized"] and evs[1].reason == "timeout"


async def test_zaman_asimindan_sonraki_cumle_haksiz_yere_dusmez(server):
    # Gerçek motor aynı anda tek çözüm yapar (num_workers=1): zaman aşımına uğrayan çözüm bitmeden sıradaki
    # cümlenin süresi başlamamalı
    import threading

    class TekSlot(FakeEngine):
        def __init__(self):
            super().__init__()
            self.lock, self.delays = threading.Lock(), [0.5, 0.05]

        def transcribe(self, samples, hotwords=None):
            with self.lock:
                time.sleep(self.delays.pop(0))
                return SttResult("ikinci", 0.9)

    audio = frames((0.3, False), (0.8, True), (0.9, False), (0.8, True), (0.9, False))
    evs = await run_stream(await server(TekSlot(), timeout_s=0.2), audio)
    assert [e.type for e in evs if e.type != "speech_started"] == ["not_recognized", "final"]


async def test_motor_hatasi_taninamadi_olur(server):
    class Bozuk(FakeEngine):
        def transcribe(self, samples, hotwords=None):
            raise RuntimeError("Library cublas64_12.dll is not found")

    evs = await run_stream(await server(Bozuk()), frames((0.3, False), (1.0, True), (1.0, False)))
    assert [(e.type, e.reason) for e in evs] == [("speech_started", ""), ("not_recognized", "error")]


async def test_pcm16_16khz(server):
    eng = FakeEngine()
    target = await server(eng)
    t = np.arange(16000) / 16000
    pcm = (0.3 * 32767 * np.sin(2 * np.pi * 300 * t)).astype("<i2")
    audio = [np.zeros(8000, "<i2").tobytes(), pcm.tobytes(), np.zeros(16000, "<i2").tobytes()]
    client = GrpcStt(target)
    st = await client.open("c-3", "pcm16", sample_rate_hz=16000)
    for a in audio:
        await st.send(a)
    await st.close()
    assert [e.type for e in [e async for e in st.events()]] == ["speech_started", "final"]
    await client.aclose()


async def _raw(target, msgs):
    async with grpc.aio.insecure_channel(target) as ch:
        call = stt_pb2_grpc.SpeechToTextStub(ch).Stream(iter(msgs))
        return [e async for e in call]


async def test_gecersiz_istekler(server):
    target = await server()
    with pytest.raises(grpc.aio.AioRpcError) as e:
        await _raw(target, [stt_pb2.AudioIn(audio=b"\xd5" * 160)])               # config yok
    assert e.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    with pytest.raises(grpc.aio.AioRpcError) as e:
        await _raw(target, [stt_pb2.AudioIn(config=stt_pb2.StreamConfig(
            codec=stt_pb2.AUDIO_CODEC_PCMA, sample_rate_hz=16000))])           # G.711 16 kHz olmaz
    assert e.value.code() == grpc.StatusCode.INVALID_ARGUMENT


async def test_akis_siniri(server):
    target = await server(max_streams=1)
    client = GrpcStt(target)
    first = await client.open("c-a", "pcma")
    await first.send(frames((0.1, False))[0])
    await asyncio.sleep(0.2)
    second = await client.open("c-b", "pcma")
    with pytest.raises(SttError, match="RESOURCE_EXHAUSTED"):
        async for _ in second.events():
            pass
    await first.close()
    [e async for e in first.events()]
    await client.aclose()


async def test_saglik_kontrolu(server):
    target = await server()
    assert await GrpcStt(target).ready()
    async with grpc.aio.insecure_channel(target) as ch:
        r = await health_pb2_grpc.HealthStub(ch).Check(health_pb2.HealthCheckRequest(service="stt.v1.SpeechToText"))
    assert r.status == health_pb2.HealthCheckResponse.SERVING


async def test_baglanti_kurulamazsa_stterror():
    client = GrpcStt("127.0.0.1:1", connect_timeout_s=0.3)
    with pytest.raises(SttError):
        await client.open("c-x", "pcma")
    assert not await client.ready()
    await client.aclose()


async def test_teshis_kaydi_cumle_sesini_ve_sonucu_yazar(server, tmp_path):
    import json
    import wave

    evs = await run_stream(await server(FakeEngine(), debug_dir=str(tmp_path)),
                           frames((0.3, False), (1.0, True), (1.0, False)), hotwords=["Zeynep Arslan"])
    assert [e.type for e in evs] == ["speech_started", "final"]
    meta = json.loads((tmp_path / "c-1_001.json").read_text(encoding="utf-8"))
    assert meta["metin"] == "muhasebeden ayşe hanım" and meta["ipuclari"] == ["Zeynep Arslan"]
    with wave.open(str(tmp_path / "c-1_001.wav")) as w:
        assert w.getframerate() == 16000 and 1.0 < w.getnframes() / 16000 < 1.8
