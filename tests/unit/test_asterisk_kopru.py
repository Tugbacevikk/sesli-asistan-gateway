"""Asterisk köprüsü (tools/santral/asterisk_kopru.py): ARI olayları ↔ Gateway sözleşmesi, sahte ARI ve Gateway ile."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "santral"))
from asterisk_kopru import Bridge, GatewayBusy  # noqa: E402


class FakeAri:
    def __init__(self, originate_fails=False):
        self.log, self.n, self.originate_fails = [], 0, originate_fails

    def _id(self, p):
        self.n += 1
        return f"{p}{self.n}"

    async def answer(self, ch):
        self.log.append(("answer", ch))

    async def create_bridge(self):
        b = self._id("b")
        self.log.append(("bridge", b))
        return b

    async def add(self, b, *chs):
        self.log.append(("add", b, *chs))

    async def remove(self, b, ch):
        self.log.append(("remove", b, ch))

    async def destroy_bridge(self, b):
        self.log.append(("destroy", b))

    async def external_media(self, hp, codec):
        m = self._id("m")
        self.log.append(("media", hp, codec, m))
        return m, ("127.0.0.1", 20002)

    async def originate(self, ep, args, cid, t):
        if self.originate_fails:
            raise RuntimeError("endpoint yok")
        ch = self._id("t")
        self.log.append(("originate", ep, args, ch))
        return ch

    async def hangup(self, ch):
        self.log.append(("hangup", ch))

    async def play(self, b, media):
        self.log.append(("play", b, media))
        return "pb1"

    async def stop_playback(self, pb):
        self.log.append(("stop", pb))


class FakeGw:
    def __init__(self, busy=False):
        self.log, self.busy = [], busy

    async def create_call(self, cid, caller, called, codec):
        if self.busy:
            raise GatewayBusy("kapasite dolu")
        self.log.append(("create", cid, caller, called, codec))
        return 10002

    async def event(self, cid, body):
        self.log.append(("event", cid, body))

    async def end(self, cid, reason):
        self.log.append(("end", cid, reason))


def start(cid="c1"):
    return {"type": "StasisStart", "args": [], "channel": {"id": cid, "name": "PJSIP/1001-0001",
                                                          "caller": {"number": "1001"},
                                                          "dialplan": {"exten": "500"}}}


def bridge(**kw):
    ari, gw = FakeAri(kw.pop("originate_fails", False)), FakeGw(kw.pop("busy", False))
    return Bridge(ari, gw, "127.0.0.1", "900", ring_timeout_s=5), ari, gw


async def test_cagri_gelince_gatewaye_acilir_ve_ses_koprulenir():
    b, ari, gw = bridge()
    await b.on_event(start())
    assert gw.log == [("create", "c1", "1001", "500", "pcma"),
                      ("event", "c1", {"type": "rtp_remote", "rtp_remote": {"host": "127.0.0.1", "port": 20002}})]
    assert ("media", "127.0.0.1:10002", "pcma", "m2") in ari.log and ("add", "b1", "c1", "m2") in ari.log
    # ExternalMedia kanalının kendi StasisStart'ı yeni çağrı sayılmaz
    await b.on_event({"type": "StasisStart", "args": [], "channel": {"id": "m2", "name": "UnicastRTP/127.0.0.1"}})
    assert len(gw.log) == 2
    await b.on_event({"type": "ChannelDtmfReceived", "digit": "0", "channel": {"id": "c1"}})
    assert gw.log[-1] == ("event", "c1", {"type": "dtmf", "digit": "0"})


async def test_aktarma_cevaplaninca_asistan_cikar_ve_gateway_kapanir():
    b, ari, gw = bridge()
    await b.on_event(start())
    await b.on_action("c1", {"type": "transfer", "extension": "214", "action_id": "a1"})
    assert ("originate", "PJSIP/214", "hedef,c1", "t3") in ari.log
    assert ("play", "b1", "tone:ring;tonezone=tr") in ari.log
    await b.on_event({"type": "StasisStart", "args": ["hedef", "c1"], "channel": {"id": "t3", "name": "PJSIP/214"}})
    assert ("stop", "pb1") in ari.log and ("remove", "b1", "m2") in ari.log and ("add", "b1", "t3") in ari.log
    assert gw.log[-2:] == [("event", "c1", {"type": "transfer_result", "status": "answered"}),
                           ("end", "c1", "transferred")]
    # konuşma bitince (hedef kapattı) arayan da kapatılır; Gateway'e ikinci DELETE gitmez
    await b.on_event({"type": "ChannelDestroyed", "cause": 16, "channel": {"id": "t3"}})
    assert ("hangup", "c1") in ari.log
    await b.on_event({"type": "StasisEnd", "channel": {"id": "c1"}})
    assert [x for x in gw.log if x[0] == "end"] == [("end", "c1", "transferred")]


@pytest.mark.parametrize("cause,status", [(17, "busy"), (21, "busy"), (19, "no_answer"), (18, "no_answer"),
                                          (34, "failed")])
async def test_aktarma_sonucu_gatewaye_bildirilir_arayan_asistanla_kalir(cause, status):
    b, ari, gw = bridge()
    await b.on_event(start())
    await b.on_action("c1", {"type": "transfer", "extension": "214"})
    await b.on_event({"type": "ChannelDestroyed", "cause": cause, "channel": {"id": "t3"}})
    assert gw.log[-1] == ("event", "c1", {"type": "transfer_result", "status": status})
    assert ("stop", "pb1") in ari.log and ("hangup", "c1") not in ari.log and b.calls["c1"].media == "m2"


async def test_arayan_kapatinca_temizlik():
    b, ari, gw = bridge()
    await b.on_event(start())
    await b.on_action("c1", {"type": "transfer", "extension": "214"})
    await b.on_event({"type": "StasisEnd", "channel": {"id": "c1"}})
    assert gw.log[-1] == ("end", "c1", "hangup")
    assert {("hangup", "m2"), ("hangup", "t3"), ("destroy", "b1")} <= set(ari.log) and not b.calls


async def test_kapatma_ve_operator_eylemleri():
    b, ari, gw = bridge()
    await b.on_event(start())
    await b.on_action("c1", {"type": "hangup"})
    assert ("hangup", "c1") in ari.log and gw.log[-1] == ("end", "c1", "hangup")
    b, ari, gw = bridge()
    await b.on_event(start())
    await b.on_action("c1", {"type": "operator"})
    assert ("originate", "PJSIP/900", "hedef,c1", "t3") in ari.log
    await b.on_event({"type": "ChannelDestroyed", "cause": 19, "channel": {"id": "t3"}})
    assert ("hangup", "c1") in ari.log                       # operatöre ulaşılamadı
    await b.on_event({"type": "StasisEnd", "channel": {"id": "c1"}})
    assert gw.log[-1] == ("end", "c1", "hangup")              # Gateway oturumu yine kapanır


async def test_gateway_almazsa_dogrudan_operator():
    b, ari, gw = bridge(busy=True)
    await b.on_event(start())
    assert ("originate", "PJSIP/900", "hedef,c1", "t1") in ari.log and gw.log == []
    assert ("add", "b2", "c1") in ari.log


async def test_aranamayan_dahili_failed():
    b, ari, gw = bridge(originate_fails=True)
    await b.on_event(start())
    await b.on_action("c1", {"type": "transfer", "extension": "999"})
    assert gw.log[-1] == ("event", "c1", {"type": "transfer_result", "status": "failed"})
    await b.on_action("c1", {"type": "transfer", "extension": "../x"})
    assert gw.log[-1] == ("event", "c1", {"type": "transfer_result", "status": "failed"})
