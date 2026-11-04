"""Rehber MCP sunucusunun istemcisi (`LLM_TOOLS=mcp`, ADR-022): core.ports.RemoteToolsPort.

Bağlantıyı tek bir arka plan görevi tutar (MCP oturumu ve anyio görev grubu açıldığı görevde kapanmalı); aramalar
ona kuyrukla iletilir ve aynı oturumda eşzamanlı yürür (bir çağrının araması ötekini bekletmez). Bağlantı koparsa ya
da bir çağrı CALL_TIMEOUT_S içinde dönmezse görev yeniden bağlanır; bekleyen arama hata alır ve çağıran (ToolRegistry)
yerel aramaya düşer. Çağıranın kendi zaman sınırı (MCP_TIMEOUT_S) daha kısadır: arayan beklemez.

`mcp` modül yüklenirken içe aktarılır (~0,8 sn): bu süre Gateway açılırken ödenir, ilk aramaya binmez.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

import anyio
from mcp import Client

log = logging.getLogger(__name__)

RECONNECT_S = 2.0
CALL_TIMEOUT_S = 5.0     # bundan uzun süren çağrı: bağlantı takılmış sayılır, yeniden kurulur


class McpRehber:
    def __init__(self, target: Any, reconnect_s: float = RECONNECT_S):
        """target: MCP adresi ("http://127.0.0.1:8765/mcp") ya da testte sunucu nesnesi (bellek içi bağlantı)."""
        self.target, self.reconnect_s = target, reconnect_s
        self.connected = False
        self.down = False        # son bağlanma denemesi olmadı: aramalar beklemeden hata alır (yerele düşer)
        self._q: asyncio.Queue[tuple[str, dict[str, Any], asyncio.Future[dict[str, Any]]]] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    def _ensure(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._owner(), name="mcp-rehber")

    async def in_contacts(self, args: dict[str, Any]) -> dict[str, Any]:
        """Gateway'in InContacts parametreleri → rehber_ara(ad, birim, dahili)."""
        return await self.call("rehber_ara", {"ad": _s(args.get("Name")), "birim": _s(args.get("Department")),
                                              "dahili": _s(args.get("Extension"))})

    async def resolve_person(self, text: str) -> dict[str, Any]:
        """Akışın 2. adımı: kisi_coz(soz)."""
        return await self.call("kisi_coz", {"soz": text})

    async def find_topic(self, text: str) -> dict[str, Any]:
        """Akışın 2. adımı: konu_ara(soz)."""
        return await self.call("konu_ara", {"soz": text})

    async def call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        self._ensure()
        if self.down:
            # Sunucu düşük: zaman aşımını (MCP_TIMEOUT_S) beklemeden hata; arka plan görevi bağlanmayı denemeyi
            # sürdürür. Ölçüm 04.11.2026: beklenince yerele düşen her arama ~1 sn gecikiyordu.
            raise ConnectionError(f"MCP sunucusuna ulaşılamıyor: {_label(self.target)}")
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        await self._q.put((tool, args, fut))
        return await fut

    async def ping(self) -> bool:
        """Sağlık denetimi: sunucu cevap veriyor mu."""
        try:
            await asyncio.wait_for(self.call("mesai_durumu", {}), 2.0)
            return True
        except Exception:
            return False

    async def aclose(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        self._fail_pending(ConnectionError("MCP istemcisi kapandı"))

    async def _owner(self) -> None:
        while True:
            try:
                async with Client(self.target) as client, anyio.create_task_group() as tg:
                    if not self.connected:
                        log.info("MCP rehber sunucusuna bağlandı: %s", _label(self.target))
                    self.connected, self.down = True, False
                    while True:
                        tool, args, fut = await self._q.get()
                        if not fut.done():                    # done: çağıran zaman aşımıyla vazgeçti
                            tg.start_soon(self._one, client, tool, args, fut)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if self.connected:
                    log.warning("MCP rehber bağlantısı koptu (%s): yeniden bağlanılacak", _reason(e))
                self.connected, self.down = False, True
                self._fail_pending(ConnectionError(f"MCP sunucusuna ulaşılamıyor: {_label(self.target)}"))
                await asyncio.sleep(self.reconnect_s)

    async def _one(self, client: Any, tool: str, args: dict[str, Any], fut: asyncio.Future[dict[str, Any]]) -> None:
        try:
            with anyio.fail_after(CALL_TIMEOUT_S):
                res = await client.call_tool(tool, args)
        except Exception as e:
            if not fut.done():
                fut.set_exception(e)
            raise                                         # bağlantı bozuk ya da takılmış: yeniden bağlan
        try:
            data = _payload(res)
        except Exception as e:                            # aracın kendi hatası: bağlantı sağlam
            if not fut.done():
                fut.set_exception(e)
            return
        if not fut.done():
            fut.set_result(data)

    def _fail_pending(self, err: Exception) -> None:
        while not self._q.empty():
            _, _, fut = self._q.get_nowait()
            if not fut.done():
                fut.set_exception(err)


def _payload(res: Any) -> dict[str, Any]:
    """Araç sonucu → sözlük (yapılandırılmış içerik varsa o, yoksa metin JSON'u). Araç hatası istisnaya çevrilir."""
    if getattr(res, "is_error", False) or getattr(res, "isError", False):
        text = "".join(getattr(b, "text", "") for b in getattr(res, "content", []) or [])
        raise RuntimeError(f"MCP aracı hata döndü: {text[:200]}")
    data = getattr(res, "structured_content", None) or getattr(res, "structuredContent", None)
    if isinstance(data, dict):
        return data.get("result", data) if set(data) == {"result"} else data
    text = "".join(getattr(b, "text", "") for b in getattr(res, "content", []) or [])
    out = json.loads(text)
    if not isinstance(out, dict):
        raise ValueError("MCP aracı sözlük döndürmedi")
    return out


def _s(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ""


def _reason(e: BaseException) -> str:
    """Görev grubunun istisna grubundan ilk gerçek neden."""
    while isinstance(e, BaseExceptionGroup) and e.exceptions:
        e = e.exceptions[0]
    return str(e) or type(e).__name__


def _label(target: Any) -> str:
    return target if isinstance(target, str) else type(target).__name__
