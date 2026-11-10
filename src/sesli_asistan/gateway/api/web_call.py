"""Arayüzden deneme çağrısının ses ve santral yolu (ADR-014): tarayıcı WebSocket'i → çekirdeğin `MediaPort` /
`PbxPort` arayüzleri. Çağrı oturumu santraldekiyle aynıdır; yalnız ses (RTP yerine WebSocket) ve santral (aktarma
sonucunu arayüzdeki kişi seçer) farklıdır."""
from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator
from typing import Any

import numpy as np
from fastapi import WebSocket

from sesli_asistan.libs.audio import g711

FRAME = 160                          # 20 ms @ 8 kHz
PLAY_LEAD_S = 0.12                   # tarayıcıya bu kadar önden gönder (ağ titremesinde boşluk olmasın)


class Outbox:
    """Tarayıcıya giden her şey tek sırayla gönderilir (ses ve olaylar karışmasın, eşzamanlı gönderim olmasın)."""

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.q: asyncio.Queue[bytes | dict | None] = asyncio.Queue()
        self.task = asyncio.create_task(self._run(), name="arayuz-gonderici")

    def put(self, item: bytes | dict) -> None:
        self.q.put_nowait(item)

    async def _run(self) -> None:
        with contextlib.suppress(Exception):
            while (item := await self.q.get()) is not None:
                if isinstance(item, bytes):
                    await self.ws.send_bytes(item)
                else:
                    await self.ws.send_text(json.dumps(item, ensure_ascii=False))

    async def close(self) -> None:
        self.q.put_nowait(None)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self.task, 2)


class WsMedia:
    """Tarayıcının ses yolu → çekirdeğin `MediaPort`'u (RTP'nin yerine)."""

    def __init__(self, outbox: Outbox, codec: str = "pcma", max_queue: int = 500, text_mode: bool = False):
        self.out, self.codec, self.text_mode = outbox, codec, text_mode
        self.q: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=max_queue)
        self._buf = bytearray()
        self.opened_at = self.last = time.monotonic()
        self.received = self.sent = self.dropped = 0

    def feed(self, data: bytes) -> None:
        """Tarayıcıdan gelen PCM16 → 20 ms'lik G.711 kareleri."""
        self._buf += data
        self.last = time.monotonic()
        while len(self._buf) >= FRAME * 2:
            pcm = np.frombuffer(bytes(self._buf[:FRAME * 2]), "<i2")
            del self._buf[:FRAME * 2]
            self.received += 1
            frame = g711.encode_int16(pcm, self.codec)
            if self.q.full():
                self.q.get_nowait()
                self.dropped += 1
            self.q.put_nowait(frame)

    async def frames(self) -> AsyncIterator[bytes]:
        while (f := await self.q.get()) is not None:
            yield f

    async def play(self, pcm8k: np.ndarray) -> int:
        if not len(pcm8k):
            return 0
        loop = asyncio.get_running_loop()
        t0, n = loop.time(), 0
        self.out.put(pcm8k.astype("<i2").tobytes())
        try:
            for _ in range(0, len(pcm8k), FRAME):
                delay = t0 + n * FRAME / 8000 - PLAY_LEAD_S - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                n += 1
            return n
        except asyncio.CancelledError:
            self.out.put({"type": "clear"})   # söze girildi: tarayıcı önden aldığı sesi de atsın
            raise
        finally:
            self.sent += n

    def idle_s(self) -> float:
        return 0.0 if self.text_mode else time.monotonic() - self.last   # yazarak denemede ses gelmez

    def stats(self) -> dict[str, int]:
        return {"paket": self.received, "gonderilen": self.sent, "kuyruk_tasmasi": self.dropped}

    def close(self) -> None:
        with contextlib.suppress(asyncio.QueueFull):
            self.q.put_nowait(None)


class WebPbx:
    """Santralin yerine: eylemi arayüze gösterir; aktarma sonucunu arayüzdeki kişi seçer."""

    def __init__(self, outbox: Outbox):
        self.out = outbox

    async def send_action(self, call_id: str, action: dict[str, Any]) -> None:
        self.out.put({"type": "pbx_action", "action": action["type"], "extension": action.get("extension"),
                      "action_id": action["action_id"]})
