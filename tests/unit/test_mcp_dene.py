"""Yerel LLM + MCP deneyi (tools/mcp): araç çevirisi, döngü, sunucudaki aktarma kuralı. mcp paketi yoksa atlanır."""
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")
import httpx  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "mcp"))
import ollama_dene as d  # noqa: E402
import rehber_sunucu  # noqa: E402
from mcp import Client  # noqa: E402


async def test_mcp_araclari_ollama_bicimine_cevrilir_ve_aktarma_kurali_sunucuda():
    async with Client(rehber_sunucu.build()) as mcp:
        tools = d.to_ollama_tools((await mcp.list_tools()).tools)
        names = {t["function"]["name"] for t in tools}
        assert names == {"rehber_ara", "aktar", "mesai_durumu"}
        ara = next(t for t in tools if t["function"]["name"] == "rehber_ara")["function"]
        assert ara["parameters"]["type"] == "object"
        assert set(ara["parameters"]["properties"]) == {"ad", "birim", "dahili"}
        # model ne derse desin sunucu rehberde olmayan numaraya aktarmaz
        for bad in ("05321234567", "999", "Muhasebe"):
            r = json.loads((await mcp.call_tool("aktar", {"dahili": bad})).content[0].text)
            assert r["durum"] == "reddedildi"
        assert json.loads((await mcp.call_tool("aktar", {"dahili": "214"})).content[0].text)["durum"] == "aktariliyor"


async def test_ollama_dongusu_tool_calls_mcp_ye_gider_sonuc_tool_mesajiyla_doner():
    seen = []
    replies = iter([
        {"message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "rehber_ara", "arguments": {"ad": "Ayşe Yılmaz"}}}]}, "prompt_eval_count": 812},
        {"message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "aktar", "arguments": {"dahili": "214"}}}]}},
        {"message": {"role": "assistant", "content": "Sizi Ayşe Yılmaz'a aktarıyorum."}},
    ])

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        return httpx.Response(200, json=next(replies))

    chat = d.OllamaChat("http://ollama", "qwen3.5:4b", False, transport=httpx.MockTransport(handler))
    async with Client(rehber_sunucu.build()) as mcp:
        tools = d.to_ollama_tools((await mcp.list_tools()).tools)
        run = await d.run_one(d.SCENARIOS[0], chat, mcp, tools)
    assert run.transferred == "214" and run.ok_tools and run.ok_transfer and run.prompt_tokens == 812
    assert run.answer.startswith("Sizi Ayşe") and len(run.llm_ms) == 3
    assert seen[0]["tools"] and seen[0]["think"] is False and seen[0]["stream"] is False
    tool_msgs = [m for m in seen[1]["messages"] if m["role"] == "tool"]
    assert tool_msgs[0]["tool_name"] == "rehber_ara" and '"single_high"' in tool_msgs[0]["content"]


async def test_izinsiz_aktarma_sayilir():
    class Bad:
        step = 0

        async def __call__(self, messages, tools):
            self.step += 1
            if self.step == 1:
                return {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"function": {"name": "aktar", "arguments": {"dahili": "231"}}}]}}
            return {"message": {"role": "assistant", "content": "Tamam."}}

    async with Client(rehber_sunucu.build()) as mcp:
        tools = d.to_ollama_tools((await mcp.list_tools()).tools)
        run = await d.run_one(next(s for s in d.SCENARIOS if s.id == "belirsiz"), Bad(), mcp, tools)
    assert run.transferred == "231" and not run.ok_transfer          # belirsizde sormadan aktardı: hata
