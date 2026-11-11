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
from collections.abc import AsyncIterator
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

    async def chat_stream(self, messages: list[dict[str, Any]],
                          tools: list[dict[str, Any]]) -> AsyncIterator[LlmDelta]:
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

    async def chat_json(self, messages: list[dict[str, Any]], max_tokens: int = 80) -> tuple[str, str]:
        """Yalnız JSON cevap (Ollama `format: json`, araçsız, kısa çıktı): anlama kipi (ADR-037). İkinci değer süre
        dökümü ("istem 812 token 2310 ms, üretim 41 token 960 ms, yükleme 0 ms"): yavaşlığın istemde mi üretimde mi
        olduğu görünsün (ölçüm 11.11.2026: çağrı ~4,9 sn)."""
        body = {"model": self.model, "messages": to_ollama(messages), "stream": False, "think": self.think,
                "format": "json", "keep_alive": self.keep_alive,
                "options": {"temperature": 0, "num_predict": max_tokens}}
        try:
            r = await self.http.post("/api/chat", json=body)
            r.raise_for_status()
            d = r.json()
            content = d["message"].get("content") or ""
        except (httpx.HTTPError, KeyError, ValueError) as e:
            raise LlmError(f"Ollama: {e}") from e

        def ms(key: str) -> int:
            return round((d.get(key) or 0) / 1e6)

        timing = (f"istem {d.get('prompt_eval_count', 0)} token {ms('prompt_eval_duration')} ms, "
                  f"üretim {d.get('eval_count', 0)} token {ms('eval_duration')} ms, yükleme {ms('load_duration')} ms")
        return content, timing

    async def warmup(self, timeout_s: float = 120.0, messages: list[dict[str, Any]] | None = None,
                     tools: list[dict[str, Any]] | None = None) -> None:
        """Modeli belleğe yükler (ilk çağrı yavaş olmasın). Yükleme, tur zaman aşımından (5 sn) uzun sürebilir.
        `messages` / `tools` verilirse (sistem istemi + araç tanımları) Ollama bu başı da hesaplayıp saklar: ilk
        çağrının ilk turu aynı başı yeniden hesaplamaz (canlı 05.11.2026: yalnız model yüklenince ilk tur 14,5 sn)."""
        msgs = to_ollama(messages) if messages else [{"role": "user", "content": "merhaba"}]
        body: dict[str, Any] = {"model": self.model, "messages": msgs, "stream": False, "think": self.think,
                                "keep_alive": self.keep_alive, "options": {"num_predict": 1}}
        if tools:
            body["tools"] = tools
        r = await self.http.post("/api/chat", timeout=timeout_s, json=body)
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
