"""LLM ve rehber adaptörleri (ağ yok: httpx MockTransport)."""
import json

import httpx
import pytest

from sesli_asistan.gateway.adapters.directory.json_dir import JsonDirectory
from sesli_asistan.gateway.adapters.llm.ollama import OllamaChat, to_ollama
from sesli_asistan.gateway.adapters.llm.openai_compat import OpenAiCompatChat
from sesli_asistan.gateway.core.directory import DirectoryCache
from sesli_asistan.gateway.core.ports import DirectoryPage, LlmError

HISTORY = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": "ayşe"},
    {"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "InContacts", "arguments": '{"Name": "ayşe"}'}}]},
    {"role": "tool", "tool_call_id": "c1", "name": "InContacts", "content": '{"match": "multiple"}'},
]


def test_ollama_mesaj_donusumu():
    out = to_ollama(HISTORY)
    assert out[2]["tool_calls"][0]["function"]["arguments"] == {"Name": "ayşe"}
    assert out[3] == {"role": "tool", "content": '{"match": "multiple"}', "tool_name": "InContacts"}


async def test_ollama_istek_ve_yanit():
    seen = {}

    def handler(req: httpx.Request):
        seen.update(json.loads(req.content))
        return httpx.Response(200, json={"message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "TransferCall", "arguments": {"Extension": "214"}}}]}})

    llm = OllamaChat(client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://x"))
    msg = await llm.chat(HISTORY, [{"type": "function"}])
    assert seen["think"] is False and seen["stream"] is False and seen["model"] == "qwen3.5:4b"
    assert msg.tool_calls[0].name == "TransferCall" and msg.tool_calls[0].arguments == {"Extension": "214"}
    assert msg.tool_calls[0].id


async def test_ollama_hata_llmerror_olur():
    llm = OllamaChat(client=httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(500)), base_url="http://x"))
    with pytest.raises(LlmError):
        await llm.chat(HISTORY, [])


async def test_openai_uyumlu_bozuk_arguman():
    def handler(req):
        body = json.loads(req.content)
        assert body["chat_template_kwargs"] == {"enable_thinking": False}
        return httpx.Response(200, json={"choices": [{"message": {"content": None, "tool_calls": [
            {"id": "x", "function": {"name": "InContacts", "arguments": "{bozuk"}}]}}]})

    llm = OpenAiCompatChat(client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://x"))
    msg = await llm.chat(HISTORY, [])
    assert msg.content == "" and msg.tool_calls[0].arguments == {} and msg.tool_calls[0].raw_arguments == "{bozuk"


async def test_json_rehber_ve_onbellek(tmp_path):
    f = tmp_path / "r.json"
    rows = [{"id": "1", "first_name": "Ayşe", "last_name": "Yılmaz", "department": "Muhasebe", "extension": "214"},
            {"id": "2", "first_name": "Can", "last_name": "Öztürk", "department": "Bilgi İşlem", "extension": "245"}]
    f.write_text(json.dumps({"server_time": "t1", "contacts": rows}), encoding="utf-8")
    cache = DirectoryCache(JsonDirectory(f))
    assert await cache.refresh() and len(cache.index) == 2 and cache.ready
    f.write_text(json.dumps({"server_time": "t2", "contacts": rows[:1]}), encoding="utf-8")  # Can silindi
    await cache.refresh()
    assert len(cache.index) == 1
    f.write_text("bozuk", encoding="utf-8")
    assert not await cache.refresh()            # kaynak bozuk: son kopya korunur
    assert len(cache.index) == 1


async def test_artimli_guncelleme_sunucu_saatini_kullanir():
    calls = []

    class Api:
        async def fetch(self, updated_since=None):
            calls.append(updated_since)
            if updated_since is None:
                return DirectoryPage("T1", [{"id": "1", "first_name": "A", "last_name": "B",
                                             "department": "D", "extension": "1"}], full=True)
            return DirectoryPage("T2", [{"id": "1", "deleted": True}])

    cache = DirectoryCache(Api())
    await cache.refresh()
    await cache.refresh()
    assert calls == [None, "T1"] and len(cache.index) == 0
