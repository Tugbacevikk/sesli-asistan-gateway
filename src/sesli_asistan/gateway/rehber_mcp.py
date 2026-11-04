"""Rehber MCP sunucusu (ADR-022): rehberi Model Context Protocol ile, yalnız okuma araçlarıyla sunar.

    python -m sesli_asistan.gateway.rehber_mcp          # MCP_HOST:MCP_PORT (varsayılan 127.0.0.1:8765), yol /mcp

İki kullanıcısı var:
  1. Gateway:
     - `MCP_STEP=true` (ADR-023): akış rehber → **MCP** → LLM. Rehber çözemeyince, LLM'den önce `konu_ara` sorulur
       ("faturamla ilgili" → Muhasebe). Konu listesi: `MCP_TOPICS_PATH`, boşsa ürünle gelen `gateway/konular.json`.
     - `LLM_TOOLS=mcp` (ADR-022): LLM'in rehber araması (InContacts) bu sunucuda (`rehber_ara`). Arama Gateway'inkiyle
       aynı işlevdir (`core.tools.in_contacts`), aynı rehber kaynağından (DIRECTORY_*) beslenir.
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
import json
import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from mcp.server.mcpserver import MCPServer

from .core.people import resolve_person
from .core.schedule import OfficeHours
from .core.search import DirectoryIndex, search_key
from .core.tools import in_contacts
from .core.topics import Topic, TopicTable, parse_topics

log = logging.getLogger(__name__)

# Rehber yenileme aralığı (sn). Değişmeyen dosya dizini yeniden kurmaz; Gateway'den en çok bu kadar geride kalınır.
# O arada yeni eklenen kişi LLM aramasında "bulunamadı" görünebilir; silinen kişi zaten aktarılamaz (Gateway kimliği
# kendi rehberinde arar, ADR-022).
REFRESH_S = 15.0

INSTRUCTIONS = ("Kurumun telefon rehberi (yalnız okuma). Kişi, birim ya da dahili için rehber_ara'yı kullan; "
                "unvan, birim + ad ya da ad + hitapla söylenen kişi için kisi_coz, arayanın söylediği konunun birimi "
                "için konu_ara, birimlerin listesi için birimler, mesai durumu için mesai_durumu. Rehberde olmayan "
                "bilgiyi uydurma.")


def load_topics(path: str = "") -> list[Topic]:
    """Konu listesi: `path` (kurumun listesi), boşsa ürünle gelen genel liste (gateway/konular.json)."""
    from importlib import resources

    if path:
        text = Path(path).read_text(encoding="utf-8")
    else:
        text = resources.files("sesli_asistan.gateway").joinpath("konular.json").read_text(encoding="utf-8")
    return parse_topics(json.loads(text))


def build_server(index: Callable[[], DirectoryIndex], office: OfficeHours | None = None,
                 tz: ZoneInfo | None = None, topics: list[Topic] | None = None) -> MCPServer:
    """`index`: güncel rehber dizinini veren işlev (rehber yenilendikçe aynı nesne güncellenir); `topics`: konu
    listesi (None: genel liste)."""
    srv = MCPServer("sesli-asistan-rehber", instructions=INSTRUCTIONS)
    topic_list = load_topics() if topics is None else topics
    cache: dict[str, Any] = {}

    def topic_table() -> TopicTable:
        ix = index()
        if cache.get("key") != (id(ix), ix.version):        # rehber değişince yeniden kurulur
            cache.update(key=(id(ix), ix.version), table=TopicTable(ix.contacts(), topic_list))
        table: TopicTable = cache["table"]
        return table

    @srv.tool()
    def kisi_coz(soz: str) -> dict[str, Any]:
        """Arayanın sözündeki kişiyi kurallarla çözer: unvan ("il jandarma komutanı", "muhasebe müdürü") → o makamdaki
        kişi; birim + ad ("muhasebeden Ayşe Hanım") → o birimdeki kişi; ad + hitap ("Mehmet Bey") → o addaki kişi(ler).
        adaylar: [{id, neden: unvan | birim_ad | ad}], kesin: tek aday ve doğrudan aktarılabilir mi (değilse teyit)."""
        return resolve_person(index(), soz)

    @srv.tool()
    def konu_ara(soz: str) -> dict[str, Any]:
        """Arayanın sözündeki konuyu ("faturamla ilgili", "arızam var", "sipariş vereceğim") ilgili birime eşler.
        adaylar: [{id, neden: "konu", eslesen: sözdeki konu, birim}]; konu yoksa ya da birim belirsizse boş liste."""
        return {"adaylar": topic_table().lookup(soz)}

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
    topics = load_topics(s.mcp_topics_path.strip())
    srv = build_server(lambda: directory.index, s.office(), tz, topics)
    host, port = host or s.mcp_host, port or s.mcp_port
    if host not in ("127.0.0.1", "localhost", "::1"):
        log.warning("MCP sunucusu ağa açık (%s): rehber bu adresten okunabilir", host)
    log.info("rehber MCP sunucusu: http://%s:%d/mcp (%d kayıt, %d konu%s)", host, port,
             len(list(directory.index.contacts())), len(topics), "" if s.mcp_topics_path.strip() else ", genel liste")
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
