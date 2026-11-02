"""Rehber araçlarını MCP sunucusu olarak sunar (deneme: yerel LLM + MCP, docs/olcumler.md).

Gateway'in kendi araçlarıyla (core/tools.py) aynı rehber araması ve aynı aktarma kuralı; tek fark araçların MCP
üzerinden (JSON-RPC, stdio ya da Streamable HTTP) sunulması. Böylece herhangi bir MCP istemcisi (ollmcp, LM Studio,
Open WebUI, tools/mcp/ollama_dene.py) aynı araçları kullanabilir.

    python tools/mcp/rehber_sunucu.py                          # stdio (istemci başlatır)
    python tools/mcp/rehber_sunucu.py --http 127.0.0.1:8765    # Streamable HTTP: http://127.0.0.1:8765/mcp

Araçlar:
  rehber_ara(ad, birim, dahili)  rehberde kişi / birim / dahili arar (en çok 3 aday + eşleşme sınıfı)
  aktar(dahili)                  aktarma ister: yalnız rehberde olan ve aktarılabilir dahiliye (dış hat / uydurma
                                 numara reddedilir: güvenlik kuralı sunucuda, modelin söylediğine güvenilmez)
  mesai_durumu()                 şu an mesai içi mi
Deneme sunucusudur: aktar gerçek santrale gitmez, isteği döndürür.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from mcp.server.mcpserver import MCPServer  # noqa: E402

from sesli_asistan.gateway.core.models import Contact  # noqa: E402
from sesli_asistan.gateway.core.schedule import OfficeHours  # noqa: E402
from sesli_asistan.gateway.core.search import DirectoryIndex  # noqa: E402

REHBER = ROOT / "data" / "rehber_ornek.json"


def build(rehber: Path = REHBER, mesai: str = "pzt-cum 08:30-18:00", tatiller: str = "") -> MCPServer:
    data = json.loads(rehber.read_text(encoding="utf-8"))
    index = DirectoryIndex(Contact.from_api(c) for c in data["contacts"])
    office = OfficeHours.parse(mesai, tatiller)
    srv = MCPServer("sesli-asistan-rehber",
                    instructions="Kurumun telefon rehberi. Aktarmadan önce rehber_ara ile kişiyi bul; yalnız "
                                 "rehber_ara'nın döndürdüğü dahiliye aktar.")

    @srv.tool()
    def rehber_ara(ad: str = "", birim: str = "", dahili: str = "") -> dict:
        """Rehberde kişi, birim ya da dahili numara arar. ad: kişinin adı / soyadı / unvanı; birim: birim adı
        (Muhasebe, Satış…); dahili: dahili numara. En az biri verilmeli. Dönen 'match': single_high (tek ve net),
        single_low (tek ama emin değil: arayana sor), multiple (birden çok aday: hangisi diye sor), none."""
        r = index.search(name=ad, department=birim, extension=dahili)
        return {"match": r.match.value, "toplam": r.total or len(r.candidates),
                "adaylar": [{"ad": c.contact.full_name, "birim": c.contact.department, "unvan": c.contact.title,
                             "dahili": c.contact.extension, "puan": round(c.score)} for c in r.candidates]}

    @srv.tool()
    def aktar(dahili: str) -> dict:
        """Arayanı bir dahiliye aktarır. Yalnız rehber_ara'nın döndürdüğü bir dahili kabul edilir."""
        ext = re.sub(r"\D", "", dahili or "")
        hit = next((c for c in index.contacts() if c.extension == ext), None)
        if hit is None or not hit.transferable:
            return {"durum": "reddedildi", "neden": f"{dahili!r} rehberde aktarılabilir bir dahili değil"}
        return {"durum": "aktariliyor", "dahili": ext, "ad": hit.full_name}

    @srv.tool()
    def mesai_durumu() -> dict:
        """Kurumun şu an mesai içinde olup olmadığı."""
        return {"durum": office.status(datetime.now()), "kural": mesai}

    return srv


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Rehber MCP sunucusu (deneme)")
    ap.add_argument("--rehber", type=Path, default=REHBER)
    ap.add_argument("--http", help="host:port → Streamable HTTP (yoksa stdio)")
    a = ap.parse_args(argv)
    srv = build(a.rehber)
    if a.http:
        import anyio

        host, port = a.http.rsplit(":", 1)
        anyio.run(lambda: srv.run_streamable_http_async(host=host, port=int(port)))
    else:
        srv.run("stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
