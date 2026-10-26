"""LLM adaptörü: OpenAI uyumlu Chat Completions (`/v1/chat/completions`) — ör. llama.cpp `llama-server --jinja`.

Düşünme modu `extra_body` ile kapatılır; llama.cpp için varsayılan
`{"chat_template_kwargs": {"enable_thinking": false}}`. Yine de sızan `<think>` blokları
çekirdekte (`dialog.clean_reply`) temizlenir.
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from sesli_asistan.gateway.core.ports import LlmError, LlmMessage, ToolCall

LLAMA_CPP_NO_THINK = {"chat_template_kwargs": {"enable_thinking": False}}


class OpenAiCompatChat:
    def __init__(self, base_url: str = "http://127.0.0.1:8080/v1", model: str = "local",
                 temperature: float = 0.2, timeout_s: float = 5.0, api_key: str = "yok",
                 extra_body: dict[str, Any] | None = None, client: httpx.AsyncClient | None = None):
        self.model, self.temperature = model, temperature
        self.extra_body = LLAMA_CPP_NO_THINK if extra_body is None else extra_body
        self.http = client or httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout_s,
                                                headers={"Authorization": f"Bearer {api_key}"})

    async def aclose(self) -> None:
        await self.http.aclose()

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LlmMessage:
        body = {"model": self.model, "messages": messages, "temperature": self.temperature,
                "stream": False, **self.extra_body}
        if tools:
            body["tools"] = tools
        try:
            r = await self.http.post("/chat/completions", json=body)
            r.raise_for_status()
            msg = r.json()["choices"][0]["message"]
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
            raise LlmError(f"OpenAI uyumlu uç: {e}") from e
        calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            raw = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw) if isinstance(raw, str) else raw
            except ValueError:
                args = {}
            calls.append(ToolCall(id=tc.get("id", ""), name=fn.get("name", ""),
                                  arguments=args if isinstance(args, dict) else {},
                                  raw_arguments=raw if isinstance(raw, str) else ""))
        return LlmMessage(content=msg.get("content") or "", tool_calls=calls)
