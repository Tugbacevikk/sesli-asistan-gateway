"""Hazır veri setinden STT ölçüm seti (tools/stt/hazir_set.py): indirilmiş FLEURS biçimi → hat kodlaması + doğrular."""
import importlib.util
import io
import sys
import tarfile
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/stt"))
_spec = importlib.util.spec_from_file_location("hazir_set", ROOT / "tools/stt/hazir_set.py")
assert _spec and _spec.loader
hs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hs)


def _wav(sn: float) -> bytes:
    x = (np.sin(np.arange(int(16000 * sn)) / 8) * 8000).astype("<i2")
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(x.tobytes())
    return b.getvalue()


def test_fleurs_bicimi_hat_ve_dogrular(tmp_path):
    on = tmp_path / "on"
    on.mkdir()
    (on / "test.tsv").write_text("1\ta.wav\tAnkara'da Hava.\tankarada hava\t…\n"
                                 "2\tb.wav\tİki kedi.\tiki kedi\t…\n", encoding="utf-8")
    with tarfile.open(on / "test.tar.gz", "w:gz") as t:
        for ad in ("a.wav", "b.wav", "c.wav"):                 # c.wav metinsiz: alınmaz
            veri = _wav(0.5)
            bilgi = tarfile.TarInfo(f"test/{ad}")
            bilgi.size = len(veri)
            t.addfile(bilgi, io.BytesIO(veri))
    out = tmp_path / "set"
    assert hs.main(["--adet", "0", "--hat", "g711", "--arka", "ugultu:15", "--onbellek", str(on),
                    "--cikti", str(out)]) == 0
    rows = [r.split("\t") for r in (out / "dogrular.tsv").read_text(encoding="utf-8").splitlines()]
    assert sorted(m for _, m in rows) == ["ankarada hava", "ankarada hava", "iki kedi", "iki kedi"]
    with wave.open(str(out / rows[0][0])) as w:
        assert w.getframerate() == 8000                          # G.711
