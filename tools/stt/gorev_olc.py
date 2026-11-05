"""STT ayarlarını görevle ölçer: her ayarın metni (ve güveni) asistanın diyaloğundan geçirilir; asistan doğru işi
yaptı mı (doğru kişiye / birime aktarma, operatör, kapatma, soru), yanlış bir eylem yaptı mı?

    python tools/stt/karsilastir.py okuma/k*.wav --tsv tests/eval/okuma_listesi.tsv ... --json okuma_sonuc.json
    python tools/stt/gorev_olc.py okuma_sonuc.json --rehber data/rehber_ornek.json

Kelime hata oranı (WER) adlarda yanıltır: "ayşe yılmaz'a" ile "ayşe yılmaz ile" WER'de hatalı, görevde doğrudur;
"ayşe yılmaz" yerine "ayşe kaya" ise tek kelime hatası ama yanlış kişiye aktarmadır. Bu yüzden karar bu ölçümle verilir.

Diyalog kural modunda (LLM'siz, ADR-019), gerçek eşiklerle (teyit `CONFIRM_BELOW`, tekrar `REPEAT_BELOW`) çalışır;
STT güveni karsilastir.py'nin JSON'undan gelir. "evet / hayır" cevapları açık bir teyit sorusuna (Ayşe Yılmaz),
"hiçbiri" "Ayşe Kaya mı, Ayşe Yılmaz mı?" sorusuna verilmiş sayılır. Sonuç türleri:
  doğru          : beklenen iş (doğru metinle aynı sonuç)
  teyitle doğru  : doğru kişi / birim, ama önce teyit soruldu (düşük güven)
  kaçırma        : anlamadı / tekrar istedi / bulamadı — zararsız ama arayan tekrar eder
  yanlış teyit   : başka bir kişi için teyit sorusu (arayan "hayır" der)
  YANLIŞ EYLEM   : yanlış kişiye / birime aktarma, istenmeden operatör ya da kapatma — en kötüsü
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from sesli_asistan.gateway.core.dialog import DialogAgent, DialogConfig  # noqa: E402
from sesli_asistan.gateway.core.models import Candidate, Contact  # noqa: E402
from sesli_asistan.gateway.core.phrases import _folded_words  # noqa: E402
from sesli_asistan.gateway.core.search import DirectoryIndex  # noqa: E402
from sesli_asistan.gateway.core.tools import ToolRegistry  # noqa: E402

ACTIONS = ("transfer", "operator", "hangup")


class _NoLlm:
    async def chat(self, *a: Any, **k: Any) -> Any:
        raise RuntimeError("kural modu")


class Judge:
    def __init__(self, contacts: list[Contact], cfg: DialogConfig):
        self.contacts, self.cfg = contacts, cfg
        self.confirm_target = next((c for c in contacts if c.full_name == "Ayşe Yılmaz"), contacts[0])

    def _agent(self) -> DialogAgent:
        return DialogAgent(_NoLlm(), ToolRegistry(DirectoryIndex(self.contacts)), self.cfg)

    @staticmethod
    def context(ref: str) -> str:
        w = set(_folded_words(ref))
        return "teyit" if w & {"evet", "hayir"} else "secim" if "hicbiri" in w else ""

    async def outcome(self, text: str, conf: float | None, context: str) -> str:
        a = self._agent()
        s = a.new_session("olcum")
        if context == "teyit":
            s.state.confirm = Candidate(self.confirm_target, 100)
            s.state.last_results = (s.state.confirm,)
        elif context == "secim":
            await s.handle_user("ayşe hanım")
        if not text.strip():
            return "kaçırma"
        s.stt_confidence = conf
        t = await s.handle_user(text)
        if t.actions:
            a0 = t.actions[0]
            ext = getattr(a0, "extension", "")
            return f"{a0.type}:{ext}" if ext else a0.type
        if s.state.confirm is not None:
            return f"teyit:{s.state.confirm.contact.extension}"
        if s.state.choice:
            return "soru:" + ",".join(sorted(c.contact.extension for c in s.state.choice))
        return "kaçırma"


def classify(got: str, exp: str) -> str:
    if got == exp:
        return "doğru"
    if got.startswith("teyit:") and exp == "transfer:" + got.split(":", 1)[1]:
        return "teyitle doğru"
    if got.split(":")[0] in ACTIONS:
        return "YANLIŞ EYLEM"
    if got.startswith("teyit:"):
        return "yanlış teyit"
    return "kaçırma"


async def run(data: dict[str, Any], judge: Judge, verbose: bool) -> dict[str, dict[str, Any]]:
    rows = data["satirlar"]
    names = list(data["ozet"])
    rows = [r for r in rows if r.get("dogru")]
    exp = [await judge.outcome(r["dogru"], None, judge.context(r["dogru"])) for r in rows]
    if verbose:
        for r, e in zip(rows, exp, strict=True):
            print(f"  {r['dosya']}: {r['dogru']!r} → {e}")
    out: dict[str, dict[str, Any]] = {}
    for n in names:
        counts: dict[str, int] = {}
        bad: list[str] = []
        for r, e in zip(rows, exp, strict=True):
            got = await judge.outcome(r.get(n, ""), r.get(f"{n}#guven"), judge.context(r["dogru"]))
            k = classify(got, e)
            counts[k] = counts.get(k, 0) + 1
            if k in ("YANLIŞ EYLEM", "yanlış teyit"):
                bad.append(f"{r['dosya']} {r.get(n, '')!r}→{got}")
        out[n] = {"sayilar": counts, "hatalar": bad, "RTF": data["ozet"][n].get("RTF")}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="STT ayarlarını görevle ölçer")
    ap.add_argument("json", type=Path, help="karsilastir.py --json çıktısı")
    ap.add_argument("--rehber", type=Path, default=Path("data/rehber_ornek.json"))
    ap.add_argument("--teyit-esigi", type=float, default=DialogConfig.confirm_below)
    ap.add_argument("--tekrar-esigi", type=float, default=DialogConfig.repeat_below)
    ap.add_argument("-v", action="store_true", help="beklenen sonuçları da yaz")
    a = ap.parse_args(argv)
    logging.disable(logging.WARNING)
    contacts = [Contact.from_api(c) for c in json.loads(a.rehber.read_text(encoding="utf-8")).get("contacts", [])]
    cfg = DialogConfig(organization="Ölçüm", llm_enabled=False, confirm_below=a.teyit_esigi,
                       repeat_below=a.tekrar_esigi)
    try:
        res = asyncio.run(run(json.loads(a.json.read_text(encoding="utf-8")), Judge(contacts, cfg), a.v))
    finally:
        logging.disable(logging.NOTSET)
    kinds = ["doğru", "teyitle doğru", "kaçırma", "yanlış teyit", "YANLIŞ EYLEM"]
    print("| ayar | " + " | ".join(kinds) + " | RTF |")
    print("|---|" + "---|" * (len(kinds) + 1))
    for n, v in res.items():
        print(f"| {n} | " + " | ".join(str(v['sayilar'].get(k, 0)) for k in kinds) + f" | {v['RTF']} |")
    for n, v in res.items():
        if v["hatalar"]:
            print(f"\n{n}:\n  " + "\n  ".join(v["hatalar"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
