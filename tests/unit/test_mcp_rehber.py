"""Rehber MCP sunucusu ve Gateway'in MCP istemcisi (ADR-022, LLM_TOOLS=mcp).

Bağlantı bellek içi (MCP SDK'nın sunucu nesnesiyle istemcisi); HTTP yolu aynı istemciyle canlı denemede ölçülür.
"""
import asyncio
import contextlib
import json
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from sesli_asistan.gateway.adapters.mcp.rehber import McpRehber  # noqa: E402
from sesli_asistan.gateway.core.dialog import DialogAgent, DialogConfig  # noqa: E402
from sesli_asistan.gateway.core.models import Contact  # noqa: E402
from sesli_asistan.gateway.core.ports import LlmMessage, ToolCall  # noqa: E402
from sesli_asistan.gateway.core.search import DirectoryIndex  # noqa: E402
from sesli_asistan.gateway.core.tools import CallToolState, ToolRegistry, in_contacts  # noqa: E402
from sesli_asistan.gateway.rehber_mcp import build_server  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def index():
    data = json.loads((ROOT / "data" / "rehber_ornek.json").read_text(encoding="utf-8"))
    return DirectoryIndex(Contact.from_api(c) for c in data["contacts"])


@pytest.fixture
async def remote(index):
    r = McpRehber(build_server(lambda: index))
    yield r
    await r.aclose()


QUERIES = [{"Name": "ayşe yılmaz"}, {"Name": "ayşe"}, {"Department": "muhasebe"}, {"Name": "satış müdürü"},
           {"Department": "satış müdürü"}, {"Extension": "245"}, {"Name": "ik"}, {"Name": "ferhat yıldırım"},
           {"Name": "selin aydın"}, {"Name": "hakan koç"}, {"Name": "mehmet bey", "Department": "satış"}]


@pytest.mark.parametrize("args", QUERIES)
async def test_mcp_sonucu_gateway_aramasiyla_birebir_ayni(index, remote, args):
    res, local, _ = in_contacts(index, args)
    data = await remote.in_contacts(args)
    assert data["content"] == local
    assert [(a["id"], a["puan"]) for a in data["adaylar"]] == [(c.contact.id, c.score) for c in res.candidates]


async def test_sunucu_yalniz_okur_aktarma_araci_yok(index):
    from mcp import Client

    async with Client(build_server(lambda: index)) as c:
        names = {t.name for t in (await c.list_tools()).tools}
    assert names == {"rehber_ara", "birimler", "mesai_durumu"}


async def test_silinmis_ve_aktarilamaz_kayit_disari_cikmaz(remote):
    for q in ({"Name": "selin aydın"}, {"Name": "hakan koç"}, {"Name": "genel müdür"}, {"Extension": "100"}):
        assert (await remote.in_contacts(q))["content"]["match"] == "none", q
    birimler = await remote.call("birimler", {})
    assert {b["ad"] for b in birimler["birimler"]} >= {"Muhasebe", "Satış", "Teknik Destek"}
    assert all(b["ad"] != "Yönetim" for b in birimler["birimler"])          # yalnız aktarılamaz kişinin birimi


async def test_llm_aramasi_mcp_den_aktarma_gateway_politikasindan(index, remote):
    tools = ToolRegistry(index, remote=remote)
    state = CallToolState()
    out = await tools.execute_async(ToolCall("1", "InContacts", {"Name": "ayşe yılmaz"}), state)
    assert out.content["match"] == "single_high" and tools.remote_calls == 1 and tools.remote_failures == 0
    assert [c.contact.extension for c in state.last_results] == ["214"]
    assert tools.transfer("214", state) is not None


async def test_mcp_kayit_uyduramaz(index):
    class Lying:
        async def in_contacts(self, args):
            return {"content": {"match": "single_high", "results": [{"name": "Hakan Koç", "extension": "100"}]},
                    "adaylar": [{"id": "c-108", "puan": 100}, {"id": "uydurma", "puan": 100}]}

    tools = ToolRegistry(index, remote=Lying())
    state = CallToolState()
    await tools.execute_async(ToolCall("1", "InContacts", {"Name": "genel müdür"}), state)
    assert state.last_results == ()                         # adaylar Gateway'in kendi rehberinden: yok
    assert tools.transfer("100", state) is None and tools.rejections == 1


@pytest.mark.parametrize("kind", ["hata", "yavas"])
async def test_mcp_olmazsa_yerel_arama(index, kind):
    class Broken:
        async def in_contacts(self, args):
            if kind == "yavas":
                await asyncio.sleep(5)
            raise ConnectionError("yok")

    tools = ToolRegistry(index, remote=Broken(), remote_timeout_s=0.05)
    state = CallToolState()
    out = await tools.execute_async(ToolCall("1", "InContacts", {"Name": "ayşe yılmaz"}), state)
    assert out.content["match"] == "single_high" and tools.remote_failures == 1
    assert [c.contact.extension for c in state.last_results] == ["214"]


async def test_sunucuya_ulasilamazsa_istemci_hizla_hata_verir():
    r = McpRehber("http://127.0.0.1:9/mcp", reconnect_s=0.05)
    with pytest.raises(ConnectionError, match="ulaşılamıyor"):
        await asyncio.wait_for(r.in_contacts({"Name": "ayşe"}), 10)
    assert not r.connected and r.down and not await r.ping()
    t0 = asyncio.get_running_loop().time()
    with pytest.raises(ConnectionError):                    # düşük olduğu biliniyor: zaman aşımı beklenmez
        await r.in_contacts({"Name": "ayşe"})
    assert asyncio.get_running_loop().time() - t0 < 0.05
    await r.aclose()


async def test_sunucu_dusunce_arama_beklemeden_yerele_doner(index):
    async with _http(build_server(lambda: index)) as url:
        r = McpRehber(url, reconnect_s=0.05)
        tools = ToolRegistry(index, remote=r, remote_timeout_s=1.0)
        await tools.execute_async(ToolCall("1", "InContacts", {"Name": "ayşe yılmaz"}), CallToolState())
        assert tools.remote_failures == 0
    # sunucu kapandı: ilk arama bağlantı hatasıyla düşer, sonrakiler beklemeden
    times = []
    for _ in range(5):
        t0 = asyncio.get_running_loop().time()
        out = await tools.execute_async(ToolCall("1", "InContacts", {"Name": "ayşe yılmaz"}), CallToolState())
        times.append(asyncio.get_running_loop().time() - t0)
        assert out.content["match"] == "single_high"
    assert tools.remote_failures == 5 and max(times[1:]) < 0.05, times
    await r.aclose()


async def test_diyalog_mcp_ile_bastan_sona(index, remote):
    class Llm:
        def __init__(self):
            self.replies = [LlmMessage("", [ToolCall("a", "InContacts", {"Name": "muhasebe müdürü"})]),
                            LlmMessage("", [ToolCall("b", "TransferCall", {"Extension": "214"})]),
                            LlmMessage("Sizi aktarıyorum.")]

        async def chat(self, messages, tools):
            return self.replies.pop(0)

    agent = DialogAgent(Llm(), ToolRegistry(index, remote=remote), DialogConfig(organization="D", fast_path=False,
                                                                                auto_transfer_single_high=False))
    t = await agent.new_session("m").handle_user("muhasebe müdürüyle görüşmek istiyorum")
    assert [a.extension for a in t.actions] == ["214"] and agent.tools.remote_calls == 1


async def test_ayarlarla_kurulum(index):
    from sesli_asistan.gateway.composition import build_agent
    from sesli_asistan.gateway.config import GatewaySettings
    from sesli_asistan.gateway.core.directory import DirectoryCache

    class Src:
        async def fetch(self, updated_since=None):
            raise RuntimeError

    d = DirectoryCache(Src(), index)
    a = build_agent(GatewaySettings(_env_file=None, llm_tools="mcp", mcp_url="http://127.0.0.1:9/mcp",
                                    mcp_timeout_s=0.2), d, llm=object())
    assert isinstance(a.tools.remote, McpRehber) and a.tools.remote_timeout_s == 0.2
    assert build_agent(GatewaySettings(_env_file=None), d, llm=object()).tools.remote is None


def _deneme_sunucusu():
    from mcp.server.mcpserver import MCPServer

    srv = MCPServer("deneme")

    @srv.tool()
    async def yavas(sn: float) -> dict:
        await asyncio.sleep(sn)
        return {"ok": True}

    @srv.tool()
    def hizli() -> dict:
        return {"ok": True}

    @srv.tool()
    def hatali() -> dict:
        raise ValueError("bozuk")

    return srv


async def test_yavas_arama_digerini_bekletmez():
    r = McpRehber(_deneme_sunucusu())
    slow = asyncio.create_task(r.call("yavas", {"sn": 1.0}))
    await asyncio.sleep(0.05)
    t0 = asyncio.get_running_loop().time()
    assert await asyncio.wait_for(r.call("hizli", {}), 0.5) == {"ok": True}
    assert asyncio.get_running_loop().time() - t0 < 0.5
    assert await slow == {"ok": True}
    await r.aclose()


@contextlib.asynccontextmanager
async def _http(srv):
    """Sunucuyu gerçek HTTP'de (Streamable HTTP, rastgele port) çalıştırır: Gateway'in kullandığı yol."""
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(srv.streamable_http_app(), host="127.0.0.1", port=0, log_level="error"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        server.should_exit = True
        await task


async def test_http_uzerinden_ayni_sonuc(index):
    async with _http(build_server(lambda: index)) as url:
        r = McpRehber(url)
        tools = ToolRegistry(index, remote=r)
        state = CallToolState()
        out = await tools.execute_async(ToolCall("1", "InContacts", {"Name": "satış müdürü"}), state)
        assert out.content == in_contacts(index, {"Name": "satış müdürü"})[1] and tools.remote_failures == 0
        await r.aclose()


async def test_takilan_cagri_baglantiyi_yeniler(monkeypatch):
    from sesli_asistan.gateway.adapters.mcp import rehber

    monkeypatch.setattr(rehber, "CALL_TIMEOUT_S", 0.2)
    async with _http(_deneme_sunucusu()) as url:
        r = McpRehber(url, reconnect_s=0.01)
        with pytest.raises(TimeoutError):
            await r.call("yavas", {"sn": 3.0})
        for _ in range(50):                                  # yeniden bağlanır (gerçekte ~1 sn)
            with contextlib.suppress(Exception):
                if await asyncio.wait_for(r.call("hizli", {}), 1) == {"ok": True}:
                    break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("yeniden bağlanmadı")
        await r.aclose()


async def test_arac_hatasi_baglantiyi_koparmaz():
    r = McpRehber(_deneme_sunucusu())
    assert await r.call("hizli", {}) == {"ok": True}
    task = r._task
    with pytest.raises(RuntimeError, match="hata döndü"):
        await r.call("hatali", {})
    assert await r.call("hizli", {}) == {"ok": True} and r._task is task and r.connected
    await r.aclose()
