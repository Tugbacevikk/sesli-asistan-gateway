"""Aşama 5: çağrı oturumu (durum makinesi) ve çağrı yöneticisi — sahte medya/STT/LLM/TTS/santral ile."""
import asyncio
import json
from pathlib import Path

import numpy as np
import pytest

from sesli_asistan.gateway.core.call import CallConfig, CallSession, CallState
from sesli_asistan.gateway.core.dialog import DialogAgent, DialogConfig
from sesli_asistan.gateway.core.manager import (
    BadRequest,
    CallExists,
    CallManager,
    CallNotFound,
    NewCall,
    PortPool,
    Unavailable,
)
from sesli_asistan.gateway.core.models import Contact
from sesli_asistan.gateway.core.ports import (
    Audio,
    LlmDelta,
    LlmError,
    LlmMessage,
    PbxError,
    SttError,
    SttEvent,
    ToolCall,
)
from sesli_asistan.gateway.core.search import DirectoryIndex
from sesli_asistan.gateway.core.speech import PromptLibrary, Speaker
from sesli_asistan.gateway.core.tools import ToolRegistry

ROOT = Path(__file__).resolve().parents[2]
TEXTS = DialogConfig().texts


# --- sahteler ---------------------------------------------------------------------------------------------

class Llm:
    def __init__(self, *replies):
        self.replies, self.seen = list(replies), []

    async def chat(self, messages, tools):
        self.seen.append(messages)
        r = self.replies.pop(0) if self.replies else LlmMessage("Size nasıl yardımcı olabilirim?")
        if isinstance(r, Exception):
            raise r
        return r


def tool(name, **args):
    return LlmMessage("", [ToolCall(id=f"c-{name}", name=name, arguments=args)])


class Tts:
    async def synthesize(self, text):
        return Audio(np.zeros(1600, np.int16), 8000)   # 0,2 sn


class Media:
    def __init__(self, play_s=0.05):
        self.play_s, self.played, self.closed, self.idle = play_s, 0, False, 0.0
        self.q: asyncio.Queue = asyncio.Queue()

    async def frames(self):
        while (f := await self.q.get()) is not None:
            yield f

    async def play(self, pcm):
        await asyncio.sleep(self.play_s)
        self.played += 1
        return len(pcm) // 160

    def idle_s(self):
        return self.idle

    def stats(self):
        return {"paket": 0}

    def close(self):
        self.closed = True
        self.q.put_nowait(None)


class Stream:
    def __init__(self):
        self.ev: asyncio.Queue = asyncio.Queue()
        self.sent, self.cancelled, self.dropped = 0, False, 0

    async def send(self, payload):
        self.sent += 1

    async def events(self):
        while (e := await self.ev.get()) is not None:
            if isinstance(e, Exception):
                raise e
            yield e

    async def close(self):
        self.ev.put_nowait(None)

    def cancel(self):
        self.cancelled = True
        self.ev.put_nowait(None)


class Stt:
    def __init__(self, fail=False):
        self.fail, self.stream, self.hotwords = fail, Stream(), None

    async def open(self, call_id, codec, hotwords):
        if self.fail:
            raise SttError("bağlanılamadı")
        self.hotwords = hotwords
        return self.stream


class Pbx:
    def __init__(self, fail=False):
        self.actions, self.fail = [], fail

    async def send_action(self, call_id, action):
        if self.fail:
            raise PbxError("santral yok")
        self.actions.append(action)


def index():
    data = json.loads((ROOT / "data/rehber_ornek.json").read_text(encoding="utf-8"))
    return DirectoryIndex(Contact.from_api(c) for c in data["contacts"])


class Call:
    """Bir oturum + sahteleri; olay gönderme ve bekleme yardımcıları."""

    def __init__(self, llm=None, stt=None, pbx=None, play_s=0.05, office="acik", **cfg):
        self.llm, self.stt, self.pbx = llm or Llm(), stt or Stt(), pbx or Pbx()
        self.media = Media(play_s)
        cfg_d = DialogConfig(organization="Deneme", fast_path=False, code_replies=False, llm_fallback=False)
        agent = DialogAgent(self.llm, ToolRegistry(index()), cfg_d)
        self.session = CallSession("c1", "pcma", self.media, self.stt, Speaker(Tts(), PromptLibrary()),
                                   agent.new_session("c1"), self.pbx,
                                   CallConfig(tick_s=0.02, **cfg), hotwords=["Ayşe Yılmaz"], office=office)
        self.task = None

    async def __aenter__(self):
        self.task = asyncio.create_task(self.session.run())
        await self.until(lambda: self.session.state == CallState.LISTENING)
        return self

    async def __aexit__(self, *exc):
        self.session.hangup()
        self.stats = await asyncio.wait_for(self.task, 2)

    def say(self, text, ms=1500):
        self.stt.stream.ev.put_nowait(SttEvent("speech_started"))
        self.stt.stream.ev.put_nowait(SttEvent("final", text, 0.9, 0, ms))

    async def until(self, pred, timeout=2.0):
        async def wait():
            while not pred():
                await asyncio.sleep(0.005)
        await asyncio.wait_for(wait(), timeout)


# --- akışlar ----------------------------------------------------------------------------------------------

async def test_karsilama_aktarma_ve_cevaplandi():
    llm = Llm(tool("InContacts", Name="Ayşe", Department="muhasebe"))
    async with Call(llm) as c:
        assert c.stt.hotwords == ["Ayşe Yılmaz"] and c.media.played >= 1   # karşılama çalındı
        c.media.q.put_nowait(b"\xd5" * 160)
        await c.until(lambda: c.stt.stream.sent == 1)                   # RTP → STT
        c.say("muhasebeden ayşe hanım")
        await c.until(lambda: c.pbx.actions)
        assert c.pbx.actions[0]["type"] == "transfer" and c.pbx.actions[0]["extension"] == "214"
        assert c.pbx.actions[0]["action_id"].startswith("a-")
        assert c.session.state == CallState.TRANSFERRING
        c.session.transfer_result("answered")
        await c.until(lambda: c.session.state == CallState.HANDED_OFF)
    assert c.stats.outcome == "transferred" and c.media.closed and c.stt.stream.cancelled
    t = c.stats.turns[0]
    assert t.heard == "muhasebeden ayşe hanım" and t.reply == "Sizi Ayşe Yılmaz'a aktarıyorum."
    assert t.actions == ["transfer:214"] and t.final_to_audio_s is not None and not t.barged


async def test_aktarma_cumlesine_soze_girilirse_aktarma_yapilmaz():
    # "Sizi Ayşe Yılmaz'a aktarıyorum" derken arayan "hayır, satış" diyor: aktarma iptal, yeni istek işlenir
    llm = Llm(tool("InContacts", Name="Ayşe Yılmaz"), tool("InContacts", Department="satış"))
    async with Call(llm, play_s=0.3) as c:
        c.say("ayşe yılmaz")
        await c.until(lambda: c.session.state == CallState.SPEAKING)
        await asyncio.sleep(0.05)
        c.say("hayır satış")
        await c.until(lambda: c.pbx.actions)
        assert [a["extension"] for a in c.pbx.actions] == ["229"]
        assert c.session.stats.barge_ins == 1 and c.session.stats.turns[0].barged
        assert c.session.dialog.state.pending_transfer.extension == "229"


async def test_mesgul_sonrasi_asistan_sorar_ve_dinlemeye_doner():
    llm = Llm(tool("InContacts", Name="Ayşe Yılmaz"),
              LlmMessage("Ayşe Hanım meşgul. Başka birine mi, operatöre mi aktarayım?"))
    async with Call(llm) as c:
        c.say("ayşe yılmaz")
        await c.until(lambda: c.session.state == CallState.TRANSFERRING)
        c.session.transfer_result("busy")
        await c.until(lambda: c.session.state == CallState.LISTENING)
        assert "[Santral]" in llm.seen[-1][-1]["content"] and llm.seen[-1][-1]["role"] == "user"
        assert c.session.stats.turns[-1].reply.startswith("Ayşe Hanım meşgul")
        assert c.session.dialog.state.pending_transfer is None


async def test_aktarma_sonucu_gelmezse_basarisiz_sayilir():
    llm = Llm(tool("InContacts", Name="Ayşe Yılmaz"), LlmMessage("Ulaşamadım, başka birini ister misiniz?"))
    async with Call(llm, transfer_result_timeout_s=0.1) as c:
        c.say("ayşe yılmaz")
        await c.until(lambda: c.session.stats.turns and c.session.stats.turns[-1].heard == "[santral: failed]")


async def test_kisa_gurultu_cevaplanmaz_uzun_anlasilamayan_tekrar_sorar():
    async with Call() as c:
        c.stt.stream.ev.put_nowait(SttEvent("not_recognized", start_ms=0, end_ms=300, reason="empty"))
        await asyncio.sleep(0.1)
        assert c.session.stats.turns == []
        c.stt.stream.ev.put_nowait(SttEvent("not_recognized", start_ms=0, end_ms=2000, reason="timeout"))
        await c.until(lambda: c.session.stats.turns)
        assert c.session.stats.turns[0].reply == TEXTS.not_understood
        assert c.session.dialog.state.failed_attempts == 1 and c.llm.seen == []


async def test_dusunurken_gelen_cumleler_birlestirilir_ve_cevabi_kesmez():
    class SlowLlm(Llm):
        async def chat(self, messages, tools):
            await asyncio.sleep(0.15)
            return await super().chat(messages, tools)

    llm = SlowLlm(LlmMessage("Kimi aramıştınız?"), tool("InContacts", Name="Can Öztürk"))
    async with Call(llm) as c:
        c.say("merhaba")
        await c.until(lambda: c.session.state == CallState.THINKING)
        c.say("can")
        c.say("öztürk")                     # asistan düşünürken iki cümle daha
        await c.until(lambda: c.pbx.actions)
        assert c.session.stats.barge_ins == 0     # düşünürken başlayan konuşma, sonraki cevabı kesmedi
        assert c.session.stats.turns[1].heard == "can öztürk"


async def test_llm_hatasinda_operator():
    async with Call(Llm(LlmError("bağlantı yok"))) as c:
        c.say("ayşe")
        await c.until(lambda: c.pbx.actions)
        assert c.pbx.actions[0]["type"] == "operator" and c.session.state == CallState.HANDED_OFF
    assert c.stats.outcome == "operator" and c.stats.turns[0].reply == TEXTS.error


async def test_stt_acilamazsa_hata_anonsu_ve_operator():
    c = Call(stt=Stt(fail=True))
    task = asyncio.create_task(c.session.run())
    await c.until(lambda: c.pbx.actions)
    assert c.pbx.actions[0]["type"] == "operator" and c.media.played >= 1
    c.session.hangup()
    stats = await asyncio.wait_for(task, 2)
    assert stats.outcome == "operator" and stats.reason == "stt_yok"


async def test_stt_akisi_koparsa_operator():
    async with Call() as c:
        c.stt.stream.ev.put_nowait(SttError("UNAVAILABLE"))
        await c.until(lambda: c.pbx.actions)
        assert c.pbx.actions[0]["type"] == "operator"


async def test_rtp_gelmezse_cagri_kapanir():
    c = Call(rtp_idle_timeout_s=0.1)
    task = asyncio.create_task(c.session.run())
    await c.until(lambda: c.session.state == CallState.LISTENING)
    c.media.idle = 1.0
    stats = await asyncio.wait_for(task, 2)
    assert stats.outcome == "error" and stats.reason == "rtp_zaman_asimi" and c.media.closed


async def test_dtmf_sifir_operator():
    async with Call() as c:
        c.session.dtmf("5")
        await asyncio.sleep(0.05)
        assert c.pbx.actions == []
        c.session.dtmf("0")
        await c.until(lambda: c.pbx.actions)
        assert c.pbx.actions[0]["type"] == "operator"


async def test_santral_aktarmayi_almazsa_basarisiz_aktarma_akisi():
    llm = Llm(tool("InContacts", Name="Ayşe Yılmaz"), LlmMessage("Aktaramadım, başka birini ister misiniz?"))
    async with Call(llm, pbx=Pbx(fail=True)) as c:
        c.say("ayşe yılmaz")
        await c.until(lambda: c.session.state == CallState.LISTENING and len(c.session.stats.turns) == 2)
        assert c.session.stats.turns[1].heard == "[santral: failed]"


# --- çağrı yöneticisi -------------------------------------------------------------------------------------

def manager(max_calls=2, ready=True, bad_ports=()):
    made = {}

    async def media(port, req):
        if port in bad_ports:
            raise OSError("adres kullanımda")
        made[req.call_id] = Media()
        return made[req.call_id]

    def session(req, media):
        agent = DialogAgent(Llm(), ToolRegistry(index()), DialogConfig())
        return CallSession(req.call_id, req.codec, media, Stt(), Speaker(Tts(), PromptLibrary()),
                           agent.new_session(req.call_id), Pbx(), CallConfig(tick_s=0.02))

    m = CallManager(media, session, PortPool(10000, 10003), max_calls=max_calls, ready=lambda: ready)
    return m, made


async def test_yonetici_port_kapasite_ve_tekrar():
    m, _ = manager(bad_ports={10000})
    assert await m.create(NewCall("a")) == 10001          # 10000 kullanımda: sıradaki
    with pytest.raises(CallExists):
        await m.create(NewCall("a"))
    await m.create(NewCall("b"))
    with pytest.raises(Unavailable):
        await m.create(NewCall("c"))                       # kapasite 2
    with pytest.raises(BadRequest):
        m.event("a", "transfer_result", {"status": "belki"})
    with pytest.raises(CallNotFound):
        m.event("yok", "dtmf", {"digit": "1"})
    await m.end("a")
    await m.end("a")                                       # idempotent
    await m.end("bilinmeyen")
    assert "a" not in m.sessions and m.results[0][0] == "a" and m.results[0][1].outcome == "hangup"
    assert await m.create(NewCall("c")) == 10003           # biten portun yerine sıradaki (hemen yeniden değil)
    await m.shutdown()
    assert m.active == 0


async def test_yonetici_hazir_degil_ve_gecersiz_codec():
    m, _ = manager(ready=False)
    with pytest.raises(Unavailable):
        await m.create(NewCall("a"))
    m, _ = manager()
    with pytest.raises(BadRequest):
        await m.create(NewCall("a", codec="opus"))
    with pytest.raises(BadRequest):
        await m.create(NewCall(""))


# --- TTS hatası, sessiz arayan ----------------------------------------------------------------------------

class BrokenTts:
    """İlk n seslendirme çalışır (anonslar), sonra servis düşer."""

    def __init__(self):
        self.down = False

    async def synthesize(self, text):
        from sesli_asistan.gateway.core.ports import TtsError

        if self.down:
            raise TtsError("Piper yok")
        return Audio(np.zeros(1600, np.int16), 8000)


async def _call_with_tts(tts, llm, **cfg):
    c = Call(llm, **cfg)
    lib = PromptLibrary()
    await lib.preload(tts, c.session.dialog.agent.fixed_prompts())
    c.session.speaker = Speaker(tts, lib)
    return c


async def test_tts_cevabi_seslendiremezse_operator():
    # Piper çöktü: arayan yalnızca "…operatöre bağlıyorum" yedek anonsunu duyar → gerçekten operatöre aktarılmalı
    tts = BrokenTts()
    llm = Llm(LlmMessage("Kimi aramıştınız, adını söyler misiniz?"))
    c = await _call_with_tts(tts, llm)
    async with c:
        tts.down = True
        c.say("merhaba")
        await c.until(lambda: c.pbx.actions)
        assert c.pbx.actions[0]["type"] == "operator" and c.session.state == CallState.HANDED_OFF
    assert c.stats.outcome == "operator" and c.stats.reason == "tts_hatasi" and c.stats.tts_failures == 1


async def test_hazir_anonslar_tts_yokken_de_calisir():
    # "Anlayamadım" hazır sesten çalınır: TTS düşük olsa da çağrı sürer
    tts = BrokenTts()
    c = await _call_with_tts(tts, Llm())
    async with c:
        tts.down = True
        c.stt.stream.ev.put_nowait(SttEvent("not_recognized", start_ms=0, end_ms=2000, reason="timeout"))
        await c.until(lambda: c.session.stats.turns)
        await c.until(lambda: c.session.state == CallState.LISTENING)
        assert c.pbx.actions == [] and c.session.stats.tts_failures == 0


async def test_sessiz_arayan_once_uyarilir_sonra_operatore():
    async with Call(no_input_s=0.15, no_input_max=2) as c:
        await c.until(lambda: c.session.stats.turns)
        assert c.session.stats.no_inputs == 1 and c.session.stats.turns[-1].reply == TEXTS.no_input
        assert c.pbx.actions == []
        await c.until(lambda: c.pbx.actions)
        assert c.pbx.actions[0]["type"] == "operator" and c.session.stats.no_inputs == 2
    assert c.stats.reason == "sessiz"


async def test_konusan_arayan_sessiz_sayilmaz():
    llm = Llm(LlmMessage("Kimi aramıştınız?"), LlmMessage("Adını tekrar söyler misiniz?"))
    async with Call(llm, no_input_s=0.3) as c:
        c.stt.stream.ev.put_nowait(SttEvent("speech_started"))     # uzun konuşma sürüyor, FINAL henüz yok
        await asyncio.sleep(0.5)
        assert c.session.stats.no_inputs == 0
        c.stt.stream.ev.put_nowait(SttEvent("final", "şey", 0.9, 0, 3000))
        await c.until(lambda: c.session.stats.turns)
        assert c.session.stats.no_inputs == 0


# --- düşünme sesi ---------------------------------------------------------------------------------------------

class SlowLlm(Llm):
    def __init__(self, delay, *replies):
        super().__init__(*replies)
        self.delay = delay

    async def chat(self, messages, tools):
        await asyncio.sleep(self.delay)
        return await super().chat(messages, tools)


class CountingMedia(Media):
    def __init__(self):
        super().__init__(play_s=0.02)
        self.lengths = []

    async def play(self, pcm):
        self.lengths.append(len(pcm))
        return await super().play(pcm)


async def test_uzun_dusunmede_klavye_sesi_calar_cevapla_kesilir():
    c = Call(SlowLlm(0.6, LlmMessage("Kimi aramıştınız?")), thinking_sound_delay_s=0.1)
    c.media = c.session.media = CountingMedia()
    async with c:
        n0 = len(c.media.lengths)
        c.say("merhaba")
        await c.until(lambda: c.session.stats.turns)
        filler = c.media.lengths[n0:-1]                         # cevaptan önce çalınanlar
        assert filler and all(n == 4000 for n in filler)       # 0,5 sn'lik klavye parçaları
        played = len(c.media.lengths)
        await asyncio.sleep(0.2)
        assert len(c.media.lengths) == played                  # cevap gelince klavye sustu


async def test_hizli_cevapta_ve_kapaliyken_klavye_sesi_yok():
    for kw in ({"thinking_sound_delay_s": 1.0}, {"thinking_sound": False, "thinking_sound_delay_s": 0.0}):
        c = Call(SlowLlm(0.2, LlmMessage("Kimi aramıştınız?")), **kw)
        c.media = c.session.media = CountingMedia()
        async with c:
            n0 = len(c.media.lengths)
            c.say("merhaba")
            await c.until(lambda c=c: c.session.stats.turns)
            assert 4000 not in c.media.lengths[n0:]


def test_klavye_sesi_ve_dosyadan_yukleme(tmp_path):
    import wave

    from sesli_asistan.gateway.composition import load_thinking_sound
    from sesli_asistan.gateway.core.speech import keyboard_sound

    k = keyboard_sound()
    assert k.dtype == np.int16 and len(k) == 6 * 8000 and abs(int(np.abs(k).max()) - int(0.12 * 32767)) <= 1
    assert (np.abs(k) > 300).mean() < 0.2                       # çoğu sessizlik: arka planda tıkırtı
    f = tmp_path / "ses.wav"
    with wave.open(str(f), "wb") as w:                          # 44,1 kHz stereo, yüksek seviye
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes((np.random.default_rng(0).uniform(-0.9, 0.9, (44100, 2)) * 32767).astype("<i2").tobytes())
    x = load_thinking_sound(str(f))
    assert abs(len(x) - 8000) <= 2 and np.abs(x).max() <= int(0.12 * 32767) + 1


class HintStream(Stream):
    def __init__(self):
        super().__init__()
        self.hints = []

    async def set_hotwords(self, words):
        self.hints.append(words)


async def test_belirsiz_aramadan_sonra_adaylar_stt_ipucunda_one_alinir():
    llm = Llm(tool("InContacts", Name="Ayşe"), LlmMessage("Ayşe Yılmaz mı, Ayşe Kaya mı?"),
              LlmMessage("Size nasıl yardımcı olabilirim?"))
    stt = Stt()
    stt.stream = HintStream()
    async with Call(llm, stt=stt) as c:
        c.say("ayşe hanım")
        await c.until(lambda: c.session.state == CallState.LISTENING and c.session.stats.turns)
        assert len(stt.stream.hints) == 1
        assert set(stt.stream.hints[0][:4]) == {"Ayşe Yılmaz", "Muhasebe", "Ayşe Kaya", "Satış"}
        c.say("bilmiyorum")                       # yeni arama yok: ipucu tekrar gönderilmez
        await c.until(lambda: len(c.session.stats.turns) == 2 and c.session.state == CallState.LISTENING)
        assert len(stt.stream.hints) == 1


async def test_ipucu_desteklemeyen_akis_sorun_cikarmaz():
    llm = Llm(tool("InContacts", Name="Ayşe"), LlmMessage("Hangi Ayşe?"))
    async with Call(llm) as c:
        c.say("ayşe")
        await c.until(lambda: c.session.state == CallState.LISTENING and c.session.stats.turns)


# --- akışlı cevap (ADR-018) -------------------------------------------------------------------------------

class StreamLlm:
    """chat_stream: verilen parçaları aralarında `gap` sn bekleyerek akıtır; sonunda bütün mesaj."""
    supports_stream = True

    def __init__(self, *scripts, gap=0.15):
        self.scripts, self.gap, self.done_at, self.seen = list(scripts), gap, [], []

    async def chat(self, messages, tools):
        raise AssertionError("akış varken chat çağrılmamalı")

    async def chat_stream(self, messages, tools):
        self.seen.append(messages)
        parts, calls = self.scripts.pop(0)
        for p in parts:
            await asyncio.sleep(self.gap)
            yield LlmDelta(text=p)
        self.done_at.append(asyncio.get_running_loop().time())
        yield LlmDelta(message=LlmMessage("".join(parts), calls))


class TimedMedia(Media):
    def __init__(self, play_s=0.05):
        super().__init__(play_s)
        self.play_at = []

    async def play(self, pcm):
        self.play_at.append(asyncio.get_running_loop().time())
        return await super().play(pcm)


def stream_call(llm, play_s=0.05, **cfg):
    c = Call(llm, play_s=play_s, **cfg)
    c.media = TimedMedia(play_s)
    c.session.media = c.media
    return c


async def test_akista_ilk_cumle_llm_bitmeden_seslendirilir():
    llm = StreamLlm((["Sizi anlayamadım. ", "Adı tekrar ", "söyler misiniz?"], []), gap=0.2)
    async with stream_call(llm, thinking_sound=False) as c:
        n0 = len(c.media.play_at)
        c.say("şey")
        await c.until(lambda: c.session.state == CallState.LISTENING and c.session.stats.turns)
        plays = c.media.play_at[n0:]
        assert plays and plays[0] < llm.done_at[0]                       # ilk ses LLM bitmeden
        t = c.session.stats.turns[0]
        assert t.reply == "Sizi anlayamadım. Adı tekrar söyler misiniz?" and not t.barged
        assert len(plays) == 2                                            # iki cümle, ikisi de çalındı (tekrar yok)


async def test_akista_soze_girilirse_konusma_durur_diyalog_tamamlanir():
    llm = StreamLlm((["Bir saniye. ", "Uzun bir açıklama ", "yapıyorum şimdi."], []), gap=0.25)
    async with stream_call(llm, play_s=0.05, thinking_sound=False) as c:
        c.say("merhaba")
        await c.until(lambda: c.session.state == CallState.SPEAKING)
        await asyncio.sleep(0.1)
        c.stt.stream.ev.put_nowait(SttEvent("speech_started"))          # LLM hâlâ yazıyor
        await c.until(lambda: c.session.stats.barge_ins == 1)
        await c.until(lambda: c.session.stats.turns and c.session.state != CallState.SPEAKING, timeout=3)
        assert c.session.stats.turns[0].barged
        assert c.session.dialog.history[-1]["content"].endswith("yapıyorum şimdi.")   # geçmiş eksiksiz


async def test_akista_once_metin_sonra_arac_aktarma_yapilir():
    llm = StreamLlm((["Hemen bakıyorum. "], [ToolCall("c1", "InContacts", {"Name": "Ayşe Yılmaz"})]))
    async with stream_call(llm, thinking_sound=False) as c:
        c.say("ayşe yılmazı arıyorum")
        await c.until(lambda: c.pbx.actions)
        assert c.pbx.actions[0]["type"] == "transfer" and c.pbx.actions[0]["extension"] == "214"
        assert c.session.stats.turns[0].reply == "Sizi Ayşe Yılmaz'a aktarıyorum."


async def test_akis_operatore_donusebilecek_turda_kullanilmaz():
    # 3. başarısızlık operatöre gider: LLM'in cümlesi önceden söylenmemeli (sink kullanılmaz → chat)
    class Both(StreamLlm):
        async def chat(self, messages, tools):
            return LlmMessage("Anlayamadım.")
    llm = Both(gap=0.01)
    async with stream_call(llm, thinking_sound=False) as c:
        c.session.dialog.state.failed_attempts = 2
        c.say("hımm")
        await c.until(lambda: c.pbx.actions)
        assert c.pbx.actions[0]["type"] == "operator" and llm.seen == []


async def test_stt_akisi_hatasiz_erken_biterse_operator():
    async with Call() as c:
        c.stt.stream.ev.put_nowait(None)          # sunucu akışı kapattı (hata yok)
        await c.until(lambda: c.pbx.actions)
        assert c.pbx.actions[0]["type"] == "operator"


async def test_cagri_bitince_akisli_diyalog_iptal_edilir():
    started, cancelled = asyncio.Event(), asyncio.Event()

    class Slow(StreamLlm):
        async def chat_stream(self, messages, tools):
            started.set()
            yield LlmDelta(text="Bir saniye. ")
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.set()
                raise
            yield LlmDelta(message=LlmMessage("x"))

    c = stream_call(Slow(), thinking_sound=False)
    task = asyncio.create_task(c.session.run())
    await c.until(lambda: c.session.state == CallState.LISTENING)
    c.say("merhaba")
    await asyncio.wait_for(started.wait(), 2)
    await asyncio.sleep(0.1)
    c.session.hangup()
    await asyncio.wait_for(task, 3)
    assert cancelled.is_set()


async def test_yonetici_oturum_kurulamazsa_port_ve_ses_sizmaz():
    closed = []

    class M(Media):
        def close(self):
            closed.append(1)

    async def media(port, req):
        return M()

    def boom(req, m):
        raise RuntimeError("kurulamadı")

    pool = PortPool(20000, 20000)
    mgr = CallManager(media, boom, pool, max_calls=2)
    with pytest.raises(RuntimeError):
        await mgr.create(NewCall("x"))
    assert closed == [1] and pool.available == 1 and mgr.active == 0
