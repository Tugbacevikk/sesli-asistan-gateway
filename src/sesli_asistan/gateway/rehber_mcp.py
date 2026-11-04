"""Rehber MCP sunucusu (ADR-022): rehberi Model Context Protocol ile, yalnız okuma araçlarıyla sunar.

    python -m sesli_asistan.gateway.rehber_mcp          # MCP_HOST:MCP_PORT (varsayılan 127.0.0.1:8765), yol /mcp

İki kullanıcısı var:
  1. Gateway (`LLM_TOOLS=mcp`): LLM'in rehber araması (InContacts) bu sunucuda yapılır. Arama Gateway'inkiyle aynı
     işlevdir (`core.tools.in_contacts`), aynı rehber kaynağından (DIRECTORY_*) beslenir ve aynı aralıkla yenilenir.
  2. MCP istemcileri (MCP Inspector, Claude Desktop, ollmcp…): rehberi sorgulamak için.

Güvenlik:
  - Araçlar yalnız okur: aktarma ve operatöre bağlama bu sunucuda YOKTUR; yalnız Gateway yapar (TransferPolicy).
    Deneme sunucusundaki `aktar` aracı bu yüzden alınmadı (tools/mcp/rehber_sunucu.py).
  - Varsayılan yalnız 127.0.0.1'i dinler (aynı makinedeki Gateway). Ağa açmak (`MCP_HOST`) bilinçli bir karardır:
    rehberdeki adlar, unvanlar ve dahililer okunabilir hâle gelir.
  - Silinmiş ve aktarılamaz kayıtlar aramaya hiç girmez (DirectoryIndex), dolayısıyla dışarı da çıkmaz.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from mcp.server.mcpserver import MCPServer

from .core.schedule import OfficeHours
from .core.search import DirectoryIndex, search_key
from .core.tools import in_contacts

log = logging.getLogger(__name__)

# Rehber yenileme aralığı (sn). Değişmeyen dosya dizini yeniden kurmaz; Gateway'den en çok bu kadar geride kalınır.
# O arada yeni eklenen kişi LLM aramasında "bulunamadı" görünebilir; silinen kişi zaten aktarılamaz (Gateway kimliği
# kendi rehberinde arar, ADR-022).
REFRESH_S = 15.0

INSTRUCTIONS = ("Kurumun telefon rehberi (yalnız okuma). Kişi, birim ya da dahili için rehber_ara'yı kullan; "
                "birimlerin listesi için birimler, mesai durumu için mesai_durumu. Rehberde olmayan bilgiyi uydurma.")


def build_server(index: Callable[[], DirectoryIndex], office: OfficeHours | None = None,
                 tz: ZoneInfo | None = None) -> MCPServer:
    """`index`: güncel rehber dizinini veren işlev (rehber yenilendikçe aynı nesne güncellenir)."""
    srv = MCPServer("sesli-asistan-rehber", instructions=INSTRUCTIONS)

    @srv.tool()
    def rehber_ara(ad: str = "", birim: str = "", dahili: str = "") -> dict[str, Any]:
        """Rehberde kişi, birim ya da dahili numara arar. ad: kişinin adı, soyadı ya da unvanı ("satış müdürü");
        birim: birim adı ("Muhasebe"); dahili: dahili numara ("214"). En az biri verilmeli.
        content.match: single_high (tek ve net), single_low (tek ama emin değil), multiple (birden çok aday),
        none (bulunamadı). content.results: adaylar (ad, birim, unvan, dahili, puan)."""
        res, content, _ = in_contacts(index(), {"Name": ad, "Department": birim, "Extension": dahili})
        return {"content": content, "adaylar": [{"id": c.contact.id, "puan": c.score} for c in res.candidates]}

    @srv.tool()
    def birimler() -> dict[str, Any]:
        """Rehberdeki birimler: ad, eş anlamlıları ve varsa birim hattı (dahili)."""
        out: dict[str, dict[str, Any]] = {}
        for c in index().contacts():
            if not c.department:
                continue
            d = out.setdefault(search_key(c.department), {"ad": c.department, "es_anlamlilar": [], "hat": None,
                                                          "kisi_sayisi": 0})
            d["es_anlamlilar"] = sorted({*d["es_anlamlilar"], *c.department_aliases})
            if c.kind == "department":
                d["hat"] = c.extension
            else:
                d["kisi_sayisi"] += 1
        return {"birimler": sorted(out.values(), key=lambda d: d["ad"])}

    @srv.tool()
    def mesai_durumu() -> dict[str, Any]:
        """Kurumun şu an mesai içinde olup olmadığı: acik | mesai_disi | tatil."""
        if office is None:
            return {"durum": "bilinmiyor"}
        return {"durum": office.status(datetime.now(tz))}

    return srv


async def serve(host: str = "", port: int = 0) -> None:
    """Ayarlarla (gateway.env) rehberi yükler, yeniler ve sunucuyu çalıştırır."""
    from .composition import build_directory
    from .config import GatewaySettings

    s = GatewaySettings()
    # Arayüzden yüklenen rehberi Gateway hemen görür; bu süreç dosyayı en çok REFRESH_S sn sonra okur
    directory = build_directory(s.model_copy(update={"directory_refresh_s": min(s.directory_refresh_s, REFRESH_S)}))
    if not await directory.refresh():
        log.warning("rehber ilk yüklemede okunamadı; boş başlıyor, %s sn'de bir yeniden denenecek",
                    s.directory_refresh_s)
    directory.start()
    tz = ZoneInfo(s.timezone.strip()) if s.timezone.strip() else None
    srv = build_server(lambda: directory.index, s.office(), tz)
    host, port = host or s.mcp_host, port or s.mcp_port
    if host not in ("127.0.0.1", "localhost", "::1"):
        log.warning("MCP sunucusu ağa açık (%s): rehber bu adresten okunabilir", host)
    log.info("rehber MCP sunucusu: http://%s:%d/mcp (%d kayıt)", host, port, len(list(directory.index.contacts())))
    try:
        await srv.run_streamable_http_async(host=host, port=port)
    finally:
        await directory.stop()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(serve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
