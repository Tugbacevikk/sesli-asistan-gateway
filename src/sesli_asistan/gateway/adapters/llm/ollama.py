"""LLM adaptörü: Ollama yerel API (`/api/chat`).

Neden OpenAI uyumlu uç (`/v1`) değil: düşünme modunu kapatmak gecikme için kritik (tasarim.md 5.3).
Ollama bunu yerel API'de `think: false` ile belgeliyor; OpenAI uyumlu uçta aynı kontrolün
desteklendiği belgelenmemiş. llama.cpp'ye geçilirse `openai_compat.py` kullanılır; çekirdek
yalnızca `LlmPort` arayüzünü bilir, değişmez.

Mesaj biçimi: çekirdek OpenAI biçiminde geçmiş tutar (tool_calls[].function.arguments = JSON metni,
araç sonucu tool_call_id ile). Burada Ollama biçimine çevrilir (arguments = nesne, araç sonucu tool_name ile).
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import httpx

from sesli_asistan.gateway.core.ports import LlmDelta, LlmError, LlmMessage, ToolCall


class OllamaChat:
    supports_stream = True
    def __init__(self, base_url: str = "http://127.0.0.1:11434", model: str = "qwen3.5:4b",
                 temperature: float = 0.2, timeout_s: float = 5.0, think: bool = False,
                 keep_alive: str = "30m", client: httpx.AsyncClient | None = None):
        self.model, self.temperature, self.think, self.keep_alive = model, temperature, think, keep_alive
        self.http = client or httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout_s)

    async def aclose(self) -> None:
        await self.http.aclose()

    def _body(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], stream: bool) -> dict[str, Any]:
        return {"model": self.model, "messages": to_ollama(messages), "tools": tools, "stream": stream,
                "think": self.think, "keep_alive": self.keep_alive, "options": {"temperature": self.temperature}}

    async def chat_stream(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]):
        """Akışlı cevap (ADR-018): metin parçaları geldikçe; sonunda bütün mesaj (araç çağrıları dahil)."""
        content, calls = "", []
        try:
            async with self.http.stream("POST", "/api/chat", json=self._body(messages, tools, True)) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    d = json.loads(line)
                    if d.get("error"):
                        raise LlmError(f"Ollama: {d['error']}")
                    m = d.get("message") or {}
                    if m.get("content"):
                        content += m["content"]
                        yield LlmDelta(text=m["content"])
                    calls += _calls(m.get("tool_calls"))
                    if d.get("done"):
                        break
        except (httpx.HTTPError, ValueError) as e:
            raise LlmError(f"Ollama: {e}") from e
        yield LlmDelta(message=LlmMessage(content=content, tool_calls=calls))

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LlmMessage:
        body = self._body(messages, tools, False)
        try:
            r = await self.http.post("/api/chat", json=body)
            r.raise_for_status()
            msg = r.json()["message"]
        except (httpx.HTTPError, KeyError, ValueError) as e:
            raise LlmError(f"Ollama: {e}") from e
        return LlmMessage(content=msg.get("content") or "", tool_calls=_calls(msg.get("tool_calls")))

    async def warmup(self, timeout_s: float = 120.0) -> None:
        """Modeli belleğe yükler (ilk çağrı yavaş olmasın). Yükleme, tur zaman aşımından (5 sn) uzun sürebilir."""
        r = await self.http.post("/api/chat", timeout=timeout_s, json={
            "model": self.model, "messages": [{"role": "user", "content": "merhaba"}], "stream": False,
            "think": self.think, "keep_alive": self.keep_alive, "options": {"num_predict": 1}})
        r.raise_for_status()


def _calls(raw_calls: list[dict[str, Any]] | None) -> list[ToolCall]:
    calls = []
    for tc in raw_calls or []:
        fn = tc.get("function", {})
        args = fn.get("arguments")
        raw = ""
        if isinstance(args, str):
            raw = args
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        calls.append(ToolCall(id=tc.get("id") or f"call_{uuid.uuid4().hex[:8]}", name=fn.get("name", ""),
                              arguments=args if isinstance(args, dict) else {}, raw_arguments=raw))
    return calls


def to_ollama(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out, names = [], {}
    for m in messages:
        if m["role"] == "assistant" and m.get("tool_calls"):
            calls = []
            for tc in m["tool_calls"]:
                fn = tc["function"]
                names[tc["id"]] = fn["name"]
                try:
                    args = json.loads(fn["arguments"]) if isinstance(fn["arguments"], str) else fn["arguments"]
                except ValueError:
                    args = {}
                calls.append({"function": {"name": fn["name"], "arguments": args}})
            out.append({"role": "assistant", "content": m.get("content") or "", "tool_calls": calls})
        elif m["role"] == "tool":
            out.append({"role": "tool", "content": m["content"],
                        "tool_name": m.get("name") or names.get(m.get("tool_call_id"), "")})
        else:
            out.append({"role": m["role"], "content": m["content"]})
    return out
