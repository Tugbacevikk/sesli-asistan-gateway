"""Aşama 3: seslendirme (metin hazırlama, cümle bölme, boru hattı, barge-in, yedek anons) ve Piper adaptörü."""
import asyncio
import io
import json
import time
import wave

import httpx
import numpy as np
import pytest

from sesli_asistan.gateway.adapters.tts.cache import CachingTts
from sesli_asistan.gateway.adapters.tts.piper_http import PiperHttpTts, parse_wav
from sesli_asistan.gateway.core.ports import Audio, TtsError
from sesli_asistan.gateway.core.speech import (
    LINE_RATE,
    PromptLibrary,
    Speaker,
    prepare_for_tts,
    split_sentences,
    to_line,
)


def wav_bytes(seconds=0.5, rate=22050, channels=1, amp=0.5) -> bytes:
    t = np.arange(int(seconds * rate)) / rate
    x = (amp * 32767 * np.sin(2 * np.pi * 300 * t)).astype("<i2")
    if channels == 2:
        x = np.repeat(x, 2)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(x.tobytes())
    return buf.getvalue()


class FakeTts:
    def __init__(self, delay=0.0, fail_on: set[int] | None = None, seconds=0.3):
        self.delay, self.fail_on, self.seconds = delay, fail_on or set(), seconds
        self.calls: list[str] = []
        self.started: list[float] = []

    async def synthesize(self, text):
        self.calls.append(text)
        self.started.append(time.perf_counter())
        if len(self.calls) in self.fail_on:
            raise TtsError("servis yok")
        await asyncio.sleep(self.delay)
        return parse_wav(wav_bytes(self.seconds))


class Out:
    def __init__(self, play_s=0.0):
        self.play_s, self.parts, self.started = play_s, [], []

    async def play(self, pcm):
        self.started.append(time.perf_counter())
        await asyncio.sleep(self.play_s)
        self.parts.append(pcm)
        return len(pcm) // 160


# --- metin ---

@pytest.mark.parametrize("src,out", [
    ("Dahili 214.", "Dahili iki yüz on dört."),
    ("Dahili 2-1-4", "Dahili iki bir dört"),
    ("**Merhaba** [Santral]", "Merhaba Santral"),
    ("%5 indirim, 3,5 puan", "yüzde beş indirim, üç virgül beş puan"),
])
def test_tts_metni(src, out):
    assert prepare_for_tts(src) == out


def test_cumle_bolme():
    assert split_sentences("Dr. Ayşe Yılmaz meşgul. Başka birine mi aktarayım? Tamam!") == \
        ["Dr. Ayşe Yılmaz meşgul.", "Başka birine mi aktarayım?", "Tamam!"]
    assert split_sentences("Sonu noktasız cümle") == ["Sonu noktasız cümle"]
    long = "Muhasebe, " * 30 + "son."
    assert all(len(s) <= 160 for s in split_sentences(long))
    assert split_sentences("") == []


def test_telefon_sesine_cevirme_ve_tepe_siniri():
    a = parse_wav(wav_bytes(1.0, 22050, amp=1.0))
    y = to_line(a)
    assert y.dtype == np.int16 and abs(len(y) - LINE_RATE) <= 1
    assert np.max(np.abs(y)) <= int(0.89 * 32767) + 1


def test_wav_ayristirma():
    a = parse_wav(wav_bytes(0.2, 16000, channels=2))
    assert a.rate == 16000 and a.pcm.ndim == 1 and abs(a.seconds - 0.2) < 0.01
    with pytest.raises(TtsError):
        parse_wav(b"<html>hata</html>")


# --- konuşmacı ---

async def test_cumle_cumle_boru_hatti():
    # 3 cümle; her seslendirme 50 ms, her çalma 100 ms: 2. cümle 1. çalınırken hazırlanmalı
    tts, out = FakeTts(delay=0.05), Out(play_s=0.1)
    r = await Speaker(tts).say("Bir. İki. Üç.", out)
    assert r.ok and r.sentences == 3 and len(out.parts) == 3
    assert tts.started[1] < out.started[0] + 0.1          # çalma sürerken başladı
    assert r.first_audio_s < 0.1                          # ilk ses yalnızca ilk cümleyi bekledi


async def test_hazir_anons_tts_e_gitmez():
    tts = FakeTts()
    lib = PromptLibrary()
    assert await lib.preload(tts, {"error": "Şu an yardımcı olamıyorum."}) == []
    n = len(tts.calls)
    r = await Speaker(tts, lib).say("şu an  yardımcı olamıyorum.", Out())
    assert r.from_library and len(tts.calls) == n


async def test_tts_hatasinda_yedek_anons():
    lib = PromptLibrary()
    await lib.preload(FakeTts(), {"error": "Hata anonsu."})
    out = Out()
    r = await Speaker(FakeTts(fail_on={1}), lib).say("Sizi Ayşe Yılmaz'a aktarıyorum.", out)
    assert not r.ok and r.fallback_played and len(out.parts) == 1 and "servis yok" in r.error


async def test_tts_zaman_asimi():
    r = await Speaker(FakeTts(delay=1.0), timeout_s=0.05).say("Uzun sürecek.", Out())
    assert not r.ok and not r.fallback_played  # yedek anons yüklü değil


async def test_ortada_hata_yedek_calmaz():
    lib = PromptLibrary()
    await lib.preload(FakeTts(), {"error": "Hata."})
    out = Out()
    r = await Speaker(FakeTts(fail_on={2}), lib).say("Bir. İki.", out)
    assert not r.ok and r.sentences == 1 and not r.fallback_played and len(out.parts) == 1


async def test_barge_in_iptal_bekleyen_seslendirmeyi_de_durdurur():
    tts, out = FakeTts(delay=0.05), Out(play_s=0.5)
    task = asyncio.create_task(Speaker(tts).say("Bir. İki. Üç.", out))
    await asyncio.sleep(0.15)          # 1. cümle çalıyor, 2. seslendiriliyor/hazır
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.1)
    assert len(out.parts) == 0 and len(tts.calls) <= 2


async def test_anons_seslendirilemezse_listede_doner():
    missing = await PromptLibrary().preload(FakeTts(fail_on={1}), {"greeting": "Merhaba.", "error": "Hata."})
    assert missing == ["greeting"]


# --- Piper HTTP ---

@pytest.mark.parametrize("fmt", ["json", "plain", "query"])
async def test_piper_istek_bicimleri(fmt):
    seen = {}

    def handler(req: httpx.Request):
        seen.update(method=req.method, path=req.url.path, query=dict(req.url.params), body=req.content)
        return httpx.Response(200, content=wav_bytes(0.1), headers={"Content-Type": "audio/wav"})

    tts = PiperHttpTts(path="/synthesize", format=fmt, voice="tr_TR-dfki-medium", length_scale=1.1,
                       client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://x"))
    a = await tts.synthesize("merhaba")
    assert a.rate == 22050 and seen["path"] == "/synthesize"
    if fmt == "json":
        assert seen["method"] == "POST" and json.loads(seen["body"]) == {
            "text": "merhaba", "voice": "tr_TR-dfki-medium", "length_scale": 1.1}
    elif fmt == "plain":
        assert seen["body"] == b"merhaba"
    else:
        assert seen["method"] == "GET" and seen["query"] == {"text": "merhaba"}


async def test_piper_hata_ttserror():
    tts = PiperHttpTts(client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503)),
                                                base_url="http://x"))
    with pytest.raises(TtsError):
        await tts.synthesize("x")


# --- önbellek ---

async def test_onbellek_bellek_ve_disk(tmp_path):
    inner = FakeTts()
    c = CachingTts(inner, max_items=2, disk_dir=tmp_path, cache_id="ses1")
    a1 = await c.synthesize("bir")
    await c.synthesize("bir")
    assert len(inner.calls) == 1 and c.hits == 1
    await c.synthesize("iki")
    await c.synthesize("üç")                       # "bir" bellekten çıktı (LRU), diskte duruyor
    await c.synthesize("bir")
    assert len(inner.calls) == 3
    # yeni süreç (servis yeniden başladı, TTS kapalı): diskten yüklenir
    c2 = CachingTts(FakeTts(fail_on={1}), disk_dir=tmp_path, cache_id="ses1")
    a2 = await c2.synthesize("bir")
    assert np.array_equal(a1.pcm, a2.pcm) and a2.rate == a1.rate
    # ses değişince eski kayıt kullanılmaz
    c3 = CachingTts(FakeTts(fail_on={1}), disk_dir=tmp_path, cache_id="ses2")
    with pytest.raises(TtsError):
        await c3.synthesize("bir")


async def test_onbellek_bozuk_dosyayi_yeniden_uretir(tmp_path):
    c = CachingTts(FakeTts(), disk_dir=tmp_path, cache_id="x")
    (tmp_path / f"{c.key('bir')}.wav").write_bytes(b"bozuk")
    a = await c.synthesize("bir")
    assert isinstance(a, Audio) and len(c.inner.calls) == 1


# --- akışlı seslendirme (ADR-018) ---

async def _parts(*items, gap=0.0):
    for x in items:
        await asyncio.sleep(gap)
        yield x


async def test_akisli_seslendirme_cumle_geldikce():
    tts, out = FakeTts(delay=0.02), Out(play_s=0.05)
    r = await Speaker(tts).say_stream(_parts("Bir. İki.", "Üç.", gap=0.1), out)
    assert r.ok and r.sentences == 3 and tts.calls == ["Bir.", "İki.", "Üç."]
    assert r.first_audio_s < 0.2                   # ikinci parça (0,2 sn) beklenmedi


async def test_akisli_seslendirme_hata_ve_iptal():
    lib = PromptLibrary()
    await lib.preload(FakeTts(), {"error": "Hata anonsu."})
    out = Out()
    r = await Speaker(FakeTts(fail_on={1}), lib).say_stream(_parts("Merhaba."), out)
    assert not r.ok and r.fallback_played
    tts, out = FakeTts(delay=0.5), Out()
    task = asyncio.create_task(Speaker(tts).say_stream(_parts("Bir.", "İki.", gap=0.05), out))
    await asyncio.sleep(0.15)
    task.cancel()                                  # söze girme
    with pytest.raises(asyncio.CancelledError):
        await task
    assert out.parts == []


@pytest.mark.parametrize("buf,done,rest", [
    ("Merhaba. Dr. Ayşe Yılmaz toplant", ["Merhaba."], "Dr. Ayşe Yılmaz toplant"),
    ("Anlamadım. ", ["Anlamadım."], ""),
    ("Tamam.", [], "Tamam."),                    # sonrası gelmedi: sayı / kısaltma devam edebilir
    ("Merhaba. Ayşe ", ["Merhaba."], "Ayşe "),   # boşluk korunur (sonraki parça yapışmasın)
    ("Sayın Dr. ", [], "Sayın Dr. "),
])
def test_akan_metinde_tamamlanan_cumleler(buf, done, rest):
    from sesli_asistan.gateway.core.dialog import complete_sentences
    assert complete_sentences(buf) == (done, rest)
