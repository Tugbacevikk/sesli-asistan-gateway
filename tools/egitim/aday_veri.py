"""Gerçek kullanımdan eğitim adayı (v9, 26.11.2026): yanlış yönlendirme geri bildirimi ve sesli deneme raporu →
anlama modeli eğitim örneği.

Kaynaklar:
  --arayuz  egitim_adaylari.jsonl   arayüz → İzleme → "Eğitim adaylarını indir" (yanlış / doğru işaretli çağrılar)
  --sesli   sesli_rapor.json        tools/deneme/sesli_rapor.py rapor --cikti sesli_rapor.md (yanına .json yazar)
  --rehber  Gateway'in o çağrılarda kullandığı rehber (ör. data/demo_rehber.json)

Her söz için etiket (niyet aktar + ad / soyad / hitap / unvan / birim / dahili) doğru kayıttan kurulur: sözdeki
sözcükler kaydın adına, unvanına, biriminin adına ya da eş adına benzetilir. Yanlış duyulmuş sözcük ("masebe")
benzerlikle doğru biçime ("muhasebe") bağlanır: model bozuk sözden doğru anlamı öğrenir. Etiket gerçek diyalog
ajanından geçirilir (anlama_veri.dogrula): o etiketle ajan doğru dahiliye aktarmıyorsa örnek `elle_etiketle.jsonl`'a
yazılır; elle düzeltilip `--elle` ile yeniden verilebilir (her satırın "cevap" alanı JSON etiket).

    python tools/egitim/aday_veri.py --arayuz ~/Downloads/egitim_adaylari.jsonl --sesli sesli_rapor.json \\
        --rehber data/demo_rehber.json
    python tools/egitim/anlama_veri.py --ek tools/egitim/veri/aday/aday_egitim.jsonl

Not: sesli deneme cümleleri diyalog setinde de var (tests/data/diyalog_seti_sesli_deneme.yaml). Bozuk biçimleri
eğitime girdikten sonra o setin sonucu bağımsız ölçü sayılmaz; anlama_test (demo / belediye) etkilenmez.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from anlama_veri import EtiketLlm, dogrula, etiket  # noqa: E402

from sesli_asistan.gateway.core.dialog import DialogAgent, DialogConfig  # noqa: E402
from sesli_asistan.gateway.core.dialog.understand import UNDERSTAND_PROMPT  # noqa: E402
from sesli_asistan.gateway.core.models import Contact  # noqa: E402
from sesli_asistan.gateway.core.search import DirectoryIndex, fold_words, similarity, strip_suffix  # noqa: E402
from sesli_asistan.gateway.core.tools import ToolRegistry  # noqa: E402
from sesli_asistan.libs.text.normalizer import turkish_lower  # noqa: E402

BENZERLIK = 75          # yanlış duyulan sözcük → kayıttaki sözcük (strip_suffix sonrası; similarity 0–100)
HITAPLAR = {"bey": "bey", "hanim": "hanım", "hanimla": "hanım", "beyle": "bey", "hanima": "hanım", "beye": "bey"}


def yakin(w: str, t: str) -> bool:
    """Sözdeki (ekli, belki bozuk) sözcük kayıttaki sözcüğe karşılık mı: "masebeden" ~ "muhasebe"."""
    if not w or not t:
        return False
    if w == t or w.startswith(t) and len(t) >= 4:
        return True
    return max(similarity(strip_suffix(w), t), similarity(w, t)) >= BENZERLIK


def kapsar(sozcukler: list[str], ad: str) -> bool:
    """Adın bütün sözcükleri sözde (benzerlikle) geçiyor mu."""
    hedef = fold_words(ad)
    return bool(hedef) and all(any(yakin(w, t) for w in sozcukler) for t in hedef)


def etiket_adaylari(soz: str, c: Contact) -> list[str]:
    """Kayıttan kurulabilecek etiketler, en dolu olandan en sade olana (ilki doğrulanamazsa sıradaki denenir)."""
    w = fold_words(soz)
    alan: dict[str, str] = {}
    if c.first_name and kapsar(w, c.first_name):
        alan["ad"] = turkish_lower(c.first_name)
    if c.last_name and kapsar(w, c.last_name):
        alan["soyad"] = turkish_lower(c.last_name)
    if (h := next((HITAPLAR[x] for x in w if x in HITAPLAR), "")) and "ad" in alan:
        alan["hitap"] = h
    if c.title and kapsar(w, c.title):
        alan["unvan"] = turkish_lower(c.title)
    adlar = [n for n in (c.department, *c.department_aliases, *c.unit_path) if n]
    tutan = sorted((n for n in adlar if kapsar(w, n)), key=lambda n: -len(fold_words(n)))
    if tutan:
        alan["birim"] = turkish_lower(tutan[0])
    if c.extension and re.search(rf"\b{re.escape(c.extension)}\b", soz):
        alan["dahili"] = c.extension
    if not alan:
        return []
    secenekler = [alan]
    for alt in (("ad", "soyad", "hitap"), ("unvan", "birim"), ("birim",), ("ad", "soyad", "hitap", "birim")):
        sade = {k: v for k, v in alan.items() if k in alt}
        if sade and sade not in secenekler:
            secenekler.append(sade)
    return [etiket(niyet="aktar", **s) for s in secenekler]


def arayuz_adaylari(yol: Path) -> list[dict[str, Any]]:
    """Geri bildirim satırı → (söz, doğru dahili). Yanlış işaretlide doğru dahili seçilen; doğru işaretlide çağrının
    son aktarması. Her çağrının ilk anlamlı sözü (selam turu atlanır)."""
    out = []
    for satir in yol.read_text(encoding="utf-8").splitlines():
        if not satir.strip():
            continue
        r = json.loads(satir)
        hedef = r.get("dogru_dahili") if r.get("karar") == "yanlis" else next(
            (a.split(":", 1)[1] for t in reversed(r.get("turns") or []) for a in t.get("actions") or []
             if a.startswith("transfer:")), "")
        sozler = [t["heard"] for t in r.get("turns") or [] if t.get("heard") and not t["heard"].startswith("[")]
        if hedef and sozler:
            out.append({"kaynak": f"arayuz_{r.get('karar')}", "id": r.get("call_id"), "sozler": sozler,
                        "dahili": hedef})
    return out


def sesli_adaylari(yol: Path) -> list[dict[str, Any]]:
    """Sesli deneme raporu (.json): aktarma beklenen senaryoların duyulan sözleri (STT çıktısı)."""
    out = []
    for r in json.loads(yol.read_text(encoding="utf-8")):
        if not str(r.get("beklenen", "")).isdigit():
            continue
        sozler = [s.strip() for s in str(r.get("duyulan", "")).split(" / ") if s.strip()]
        if sozler:
            out.append({"kaynak": "sesli", "id": r.get("id"), "sozler": sozler, "dahili": str(r["beklenen"]),
                        "soylenen": r.get("soylenen", "")})
    return out


async def isle(adaylar: list[dict[str, Any]], rehber: Path, elle: list[dict[str, Any]]) -> tuple[list, list]:
    data = json.loads(rehber.read_text(encoding="utf-8"))
    contacts = [Contact.from_api(c) for c in data["contacts"]]
    index = DirectoryIndex(contacts)
    by_ext = {c.extension: c for c in index.source_contacts()}
    llm = EtiketLlm()
    agent = DialogAgent(llm, ToolRegistry(index), DialogConfig(understand="llm"))
    system = UNDERSTAND_PROMPT + agent._understand_departments()
    tamam, kalan = [], []

    def ornek(a: dict[str, Any], soz: str, cevap: str) -> dict[str, Any]:
        return {"rehber": rehber.stem, "tur": f"gercek_{a['kaynak']}", "dahili": a["dahili"], "messages": [
            {"role": "system", "content": system}, {"role": "user", "content": soz},
            {"role": "assistant", "content": cevap}]}

    for e in elle:      # elle düzeltilmiş etiketler: yine doğrulanır
        if await dogrula(agent, llm, e["soz"], e["cevap"], e["dahili"]):
            tamam.append(ornek(e, e["soz"], e["cevap"]))
        else:
            kalan.append({**e, "neden": "elle etiket doğrulanmadı"})
    for a in adaylar:
        c = by_ext.get(a["dahili"])
        if c is None:
            kalan.append({**a, "soz": a["sozler"][0], "cevap": "", "neden": "dahili rehberde yok"})
            continue
        # ilk söz (çoğunlukla isteğin kendisi); kayda benzeyen bir şey içermiyorsa sonrakiler denenir
        for soz in a["sozler"]:
            secenek = etiket_adaylari(soz, c)
            dogru = [x for x in secenek if await dogrula(agent, llm, soz, x, a["dahili"])]
            if dogru:
                tamam.append(ornek(a, soz, dogru[0]))
                break
        else:
            kalan.append({**a, "soz": a["sozler"][0], "cevap": etiket_adaylari(a["sozler"][0], c)[:1] or "",
                          "neden": "otomatik etiket doğrulanmadı",
                          "kayit": {"ad": c.full_name, "unvan": c.title, "birim": c.department}})
    return tamam, kalan


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--arayuz", action="append", default=[], help="arayüzden indirilen egitim_adaylari.jsonl")
    ap.add_argument("--sesli", action="append", default=[], help="sesli_rapor.json")
    ap.add_argument("--elle", action="append", default=[], help="elle düzeltilmiş elle_etiketle.jsonl")
    ap.add_argument("--rehber", required=True, help="çağrılarda kullanılan rehber (JSON)")
    ap.add_argument("--cikti", default=str(ROOT / "tools/egitim/veri/aday"))
    a = ap.parse_args(argv)
    adaylar = [x for y in a.arayuz for x in arayuz_adaylari(Path(y))]
    adaylar += [x for y in a.sesli for x in sesli_adaylari(Path(y))]
    elle = []
    for y in a.elle:
        for satir in Path(y).read_text(encoding="utf-8").splitlines():
            if satir.strip() and (e := json.loads(satir)).get("cevap") and isinstance(e["cevap"], str):
                elle.append(e)
    tamam, kalan = asyncio.run(isle(adaylar, Path(a.rehber), elle))
    out = Path(a.cikti)
    out.mkdir(parents=True, exist_ok=True)
    for ad, rows in (("aday_egitim", tamam), ("elle_etiketle", kalan)):
        with (out / f"{ad}.jsonl").open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{len(adaylar)} aday + {len(elle)} elle → eğitim örneği {len(tamam)}, elle etiketlenecek {len(kalan)}"
          f"  ({out})")
    for k in kalan[:10]:
        print(f"  [{k.get('id')}] {k.get('soz')!r} → {k.get('dahili')}: {k['neden']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
