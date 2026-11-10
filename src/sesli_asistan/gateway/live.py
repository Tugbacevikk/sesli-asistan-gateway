"""Canlı çağrı akışı (tasarim.md 12.5): süren çağrıların olayları (durum, duyulan, cevap, eylem) arayüze anında.

Santral ve deneme çağrılarının oturumu (`CallSession`) olaylarını gözlemciyle verir; burada çağrı başına son olaylar
tutulur ve abonelere (İzleme sekmesinin WebSocket'i, `/v1/ui/canli`) dağıtılır. Yeni bağlanan önce süren çağrıların
o ana kadarki olaylarını alır (anlık görüntü), sonra yenilerini.

- Kişisel veri: `CALL_HISTORY_TEXTS=false` ise metinler burada da boşaltılır (geçmişle aynı kural). Arayan numarası
  hiç girmez. Bellekte yalnız süren çağrılar ve biteli `KEEP_ENDED_S` sn olanlar tutulur.
- Yavaş abone çağrıyı yavaşlatmaz: yayın kuyruğa bırakır (bekleme yok); kuyruğu dolan abone düşürülür, tarayıcı
  yeniden bağlanır ve anlık görüntüyü yeniden alır.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime
from typing import Any

from .core.models import CallEvent

# arayüzün gösterdiği olaylar; "turn" yalnız ölçümle (metin zaten heard / reply'da)
KINDS = set(CallEvent) - {CallEvent.THINKING_SOUND}
_TEXT_FIELDS = ("text",)
_TURN_FIELDS = ("final_to_audio_s", "dialog_s", "llm_calls")


class LiveFeed:
    KEEP_ENDED_S = 30.0
    STALE_S = 4 * 3600        # "ended" hiç gelmediyse (oturum kurulamadı) bu kadar sessizlikten sonra silinir
    MAX_EVENTS = 200          # çağrı başına (uzun çağrıda bellek sınırlı)
    MAX_CALLS = 500
    QUEUE = 1000              # abone başına bekleyen olay

    def __init__(self, keep_texts: bool = True, clock: Callable[[], float] = time.monotonic):
        self.keep_texts, self.clock = keep_texts, clock
        self.calls: dict[str, dict[str, Any]] = {}
        self._subs: set[asyncio.Queue[dict[str, Any] | None]] = set()

    # --- yayın ------------------------------------------------------------------------------------------------

    def observer(self, call_id: str, channel: str) -> Callable[[str, dict[str, Any]], None]:
        self.publish(call_id, channel, CallEvent.START, {"started": datetime.now().isoformat(timespec="seconds")})
        return lambda kind, data: self.publish(call_id, channel, kind, data)

    def publish(self, call_id: str, channel: str, kind: str, data: dict[str, Any]) -> None:
        if kind not in KINDS:
            return
        if kind == CallEvent.TURN:
            data = {k: data[k] for k in _TURN_FIELDS if data.get(k) is not None}
        elif not self.keep_texts:
            data = {k: ("" if k in _TEXT_FIELDS else v) for k, v in data.items()}
        ev = {"call_id": call_id, "channel": channel, "type": kind, "t": datetime.now().strftime("%H:%M:%S"), **data}
        self._prune()
        c = self.calls.get(call_id)
        if c is None:
            if len(self.calls) >= self.MAX_CALLS:
                return
            c = self.calls[call_id] = {"call_id": call_id, "channel": channel, "state": "", "ended_at": None,
                                       "last": 0.0, "events": deque(maxlen=self.MAX_EVENTS)}
        c["last"] = self.clock()
        if kind == CallEvent.STATE:
            c["state"] = data.get("state", "")
        if kind == CallEvent.ENDED:
            c["ended_at"] = self.clock()
        c["events"].append(ev)
        for q in list(self._subs):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:          # yavaş abone: düşürülür (yeniden bağlanınca anlık görüntü alır)
                self._subs.discard(q)
                while not q.empty():
                    q.get_nowait()
                q.put_nowait(None)             # aboneye "bağlantını kapat" işareti

    def _prune(self) -> None:
        now = self.clock()
        for cid in [k for k, c in self.calls.items() if now - c["last"] > self.STALE_S
                    or (c["ended_at"] is not None and now - c["ended_at"] > self.KEEP_ENDED_S)]:
            del self.calls[cid]

    # --- abonelik ---------------------------------------------------------------------------------------------

    def snapshot(self) -> list[dict[str, Any]]:
        self._prune()
        return [{"call_id": c["call_id"], "channel": c["channel"], "state": c["state"],
                 "ended": c["ended_at"] is not None, "events": list(c["events"])} for c in self.calls.values()]

    def subscribe(self) -> asyncio.Queue[dict[str, Any] | None]:
        q: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(self.QUEUE)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any] | None]) -> None:
        self._subs.discard(q)

    @property
    def subscribers(self) -> int:
        return len(self._subs)
