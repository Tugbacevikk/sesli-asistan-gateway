import struct

from sesli_asistan.gateway.adapters.rtp import JitterBuffer, build, parse
from sesli_asistan.gateway.adapters.rtp.packet import RtpPacket


def test_olustur_ayristir():
    data = build(8, 65535, 123456, 0xDEADBEEF, b"\xd5" * 160, marker=True)
    p = parse(data)
    assert (p.pt, p.seq, p.ts, p.ssrc, p.marker, len(p.payload)) == (8, 65535, 123456, 0xDEADBEEF, True, 160)


def test_csrc_uzanti_dolgu():
    # CC=1, X=1, P=1
    hdr = struct.pack("!BBHII", 0x80 | 0x20 | 0x10 | 1, 0, 1, 2, 3)
    csrc = b"\x00" * 4
    ext = struct.pack("!HH", 0xBEDE, 1) + b"\x00" * 4
    payload = b"\x11" * 10
    pad = b"\x00\x00\x03"
    p = parse(hdr + csrc + ext + payload + pad)
    assert p.payload == payload


def test_rtcp_ve_bozuk_reddedilir():
    assert parse(b"\x80\xc8" + b"\x00" * 10) is None  # RTCP SR (pt 72)
    assert parse(b"\x40" + b"\x00" * 11) is None      # sürüm 1
    assert parse(b"\x80") is None


def _p(seq):
    return RtpPacket(0, seq, seq * 160, 1, b"x")


def test_sira_tamponu_dizer():
    jb = JitterBuffer(depth=4)
    out = []
    for s in [10, 12, 11, 13]:
        out += jb.push(_p(s))
    assert [p.seq for p in out] == [10, 11, 12, 13]


def test_kayip_none_olarak_gelir():
    jb = JitterBuffer(depth=2)
    out = []
    for s in [1, 3, 4, 5, 6]:
        out += jb.push(_p(s))
    # kayıp ilan edilince bekleyenler sırayla çıkar
    assert [None if p is None else p.seq for p in out] == [1, None, 3, 4, 5, 6]


def test_sarma_ve_gec_paket():
    jb = JitterBuffer(depth=2)
    out = []
    for s in [65534, 65535, 0, 1]:
        out += jb.push(_p(s))
    assert [p.seq for p in out] == [65534, 65535, 0, 1]
    assert jb.push(_p(65535)) == []  # geç
    assert jb.late == 1
