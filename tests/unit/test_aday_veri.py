"""Gerçek kullanımdan eğitim adayı (tools/egitim/aday_veri.py): etiket kayıttan, bozuk söz doğru biçime bağlanır,
ajanla doğrulanamayan elle etiketlemeye ayrılır."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/egitim"))
_spec = importlib.util.spec_from_file_location("aday_veri", ROOT / "tools/egitim/aday_veri.py")
assert _spec and _spec.loader
aday = importlib.util.module_from_spec(_spec)
sys.modules["aday_veri"] = aday
_spec.loader.exec_module(aday)


def test_geri_bildirim_ve_sesli_rapordan_ornek(tmp_path):
    fb = [{"call_id": "c1", "karar": "yanlis", "dogru_dahili": "51021", "not": "",
           "turns": [{"heard": "merhaba", "reply": "", "actions": []},
                     {"heard": "elif kort hanımla görüşecektim", "reply": "", "actions": ["transfer:20020"]}]},
          {"call_id": "c2", "karar": "dogru", "dogru_dahili": "", "not": "",
           "turns": [{"heard": "narkotik şubesine bağlarmısınız", "reply": "", "actions": ["transfer:40300"]}]},
          {"call_id": "c3", "karar": "yanlis", "dogru_dahili": "51021", "not": "",
           "turns": [{"heard": "şey ben şeyi arıyordum", "reply": "", "actions": []}]}]
    (tmp_path / "fb.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in fb), encoding="utf-8")
    ses = [{"id": "S01", "soylenen": "mehmet demire bağlar mısınız", "duyulan": "mehmet demire bağlarmısınız",
            "beklenen": "20011"},
           {"id": "S30", "soylenen": "operatöre bağlayın", "duyulan": "operatöre bağlayın", "beklenen": "operator"}]
    (tmp_path / "ses.json").write_text(json.dumps(ses, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "out"
    assert aday.main(["--arayuz", str(tmp_path / "fb.jsonl"), "--sesli", str(tmp_path / "ses.json"),
                      "--rehber", str(ROOT / "data/demo_rehber.json"), "--cikti", str(out)]) == 0
    ok = [json.loads(x) for x in (out / "aday_egitim.jsonl").read_text(encoding="utf-8").splitlines()]
    etiket = {r["messages"][1]["content"]: json.loads(r["messages"][2]["content"]) for r in ok}
    # bozuk söz ("kort") doğru soyada bağlandı; selam turu atlandı
    assert etiket["elif kort hanımla görüşecektim"] == {"niyet": "aktar", "ad": "elif", "soyad": "kurt",
                                                       "hitap": "hanım"}
    assert etiket["narkotik şubesine bağlarmısınız"]["birim"] == "narkotik şube"
    assert etiket["mehmet demire bağlarmısınız"] == {"niyet": "aktar", "ad": "mehmet", "soyad": "demir"}
    assert {r["tur"] for r in ok} == {"gercek_arayuz_yanlis", "gercek_arayuz_dogru", "gercek_sesli"}
    assert ok[0]["messages"][0]["role"] == "system"
    elle = [json.loads(x) for x in (out / "elle_etiketle.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [e["id"] for e in elle] == ["c3"]                          # kayda benzeyen söz yok: elle


def test_elle_duzeltilen_etiket_dogrulanir(tmp_path):
    elle = [{"kaynak": "arayuz_yanlis", "id": "c3", "soz": "şey ben şeyi arıyordum elif hanım", "dahili": "51021",
             "cevap": '{"niyet":"aktar","ad":"elif","hitap":"hanım"}'},
            {"kaynak": "arayuz_yanlis", "id": "c4", "soz": "x", "dahili": "51021",
             "cevap": '{"niyet":"aktar","ad":"mehmet","soyad":"demir"}'}]           # yanlış kişi: geçmez
    (tmp_path / "elle.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in elle),
                                         encoding="utf-8")
    out = tmp_path / "out"
    aday.main(["--elle", str(tmp_path / "elle.jsonl"), "--rehber", str(ROOT / "data/demo_rehber.json"),
               "--cikti", str(out)])
    assert len((out / "aday_egitim.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    kalan = [json.loads(x) for x in (out / "elle_etiketle.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [k["id"] for k in kalan] == ["c4"]
