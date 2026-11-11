"""GEÇİCİ tanı testi (silinecek): R25 / R26 sözlerinde hangi adımın cevap verdiğini CI çıktısında gösterir."""
import json
import logging
from pathlib import Path

from sesli_asistan.gateway.core.dialog import DialogAgent, DialogConfig
from sesli_asistan.gateway.core.models import Contact
from sesli_asistan.gateway.core.people import resolve_person, same_title
from sesli_asistan.gateway.core.people.common import _title_words
from sesli_asistan.gateway.core.search import DirectoryIndex
from sesli_asistan.gateway.core.tools import ToolRegistry

ROOT = Path(__file__).resolve().parents[2]


def _index():
    data = json.loads((ROOT / "data/sirket_rehber.json").read_text(encoding="utf-8"))
    return DirectoryIndex(Contact.from_api(c) for c in data["contacts"])


async def test_tani(caplog):
    idx = _index()
    rapor = []
    for said in ["kaliteden sorumlu müdür hanımla görüşmem lazım", "genel müdürün sekreterine bağlar mısınız"]:
        a = DialogAgent(object(), ToolRegistry(_index()), DialogConfig(llm_enabled=False))
        s = a.new_session("t")
        s.greeting()
        with caplog.at_level(logging.DEBUG, logger="sesli_asistan.gateway.core.dialog"):
            caplog.clear()
            turn = await s.handle_user(said)
        adim = [r.getMessage() for r in caplog.records if "adım" in r.getMessage()]
        st = [f"{c.extension} {c.title} ({c.department})" for c in same_title(idx, said)]
        res = resolve_person(idx, said)
        rapor.append({"soz": said, "cevap": turn.text, "adim": adim, "same_title_n": len(st),
                      "title_words": _title_words(said),
                      "resolve": [(x.get("neden"), x["id"]) for x in res["adaylar"]], "kesin": res["kesin"]})
    raise AssertionError(json.dumps(rapor, ensure_ascii=False, indent=1))
