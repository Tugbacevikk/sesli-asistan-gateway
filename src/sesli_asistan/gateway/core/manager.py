"""Çağrı oturumlarının yaşam döngüsü (tasarim.md 5.1 CallManager, 6.2.1, 9.1).

  - Çağrı başına bir RTP portu (port havuzu; port başka süreçte kullanılıyorsa sıradaki denenir).
  - Eşzamanlı çağrı sınırı: doluysa yeni çağrı reddedilir (503), mevcut çağrıların kalitesi korunur (yük reddi).
  - Bağımlı servisler hazır değilse yeni çağrı reddedilir (503 → santral operatöre yönlendirir).
  - Aynı `call_id` ikinci kez açılamaz (409); bitirme (DELETE) idempotent'tir.
  - Her oturum kendi görevinde çalışır; bir oturumdaki hata diğerlerini etkilemez (izole hata, 9.2).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .call import TRANSFER_STATUSES, CallSession, CallStats
from .ports import MediaPort

log = logging.getLogger(__name__)

CODECS = ("pcma", "pcmu")


class CallError(Exception):
    status = 500
    code = "INTERNAL"

    def __init__(self, message: str, call_id: str | None = None):
        super().__init__(message)
        self.call_id = call_id


class BadRequest(CallError):
    status, code = 400, "BAD_REQUEST"


class CallExists(CallError):
    status, code = 409, "CALL_EXISTS"


class CallNotFound(CallError):
    status, code = 404, "CALL_NOT_FOUND"


class Unavailable(CallError):
    status, code = 503, "UNAVAILABLE"


@dataclass(frozen=True)
class NewCall:
    call_id: str
    codec: str = "pcma"
    rtp_remote: tuple[str, int] | None = None   # santralin ses alma adresi; yoksa ilk paketin kaynağı
    caller: str = ""
    called: str = ""


MediaFactory = Callable[[int, NewCall], Awaitable[MediaPort]]      # OSError: port kullanılamıyor
SessionFactory = Callable[[NewCall, MediaPort], CallSession]


class PortPool:
    """RTP port havuzu. Biten çağrının portu hemen yeniden verilmez (gecikmiş paketler yeni çağrıya karışmasın)."""

    def __init__(self, first: int, last: int):
        if not 0 < first <= last < 65536:
            raise ValueError(f"geçersiz port aralığı: {first}-{last}")
        self._free: deque[int] = deque(range(first, last + 1))
        self._used: set[int] = set()

    def take(self) -> int | None:
        if not self._free:
            return None
        p = self._free.popleft()
        self._used.add(p)
        return p

    def give_back(self, port: int) -> None:
        if port in self._used:
            self._used.discard(port)
            self._free.append(port)

    @property
    def available(self) -> int:
        return len(self._free)


class CallManager:
    def __init__(self, media_factory: MediaFactory, session_factory: SessionFactory, ports: PortPool,
                 max_calls: int = 30, ready: Callable[[], bool] = lambda: True, keep_results: int = 100,
                 on_finished: Callable[[str, CallStats], Awaitable[None]] | None = None):
        """on_finished: biten çağrının özeti (kalıcı geçmiş için); hatası çağrıyı etkilemez."""
        self.media_factory, self.session_factory, self.on_finished = media_factory, session_factory, on_finished
        self.ports, self.max_calls, self.ready = ports, max_calls, ready
        self.sessions: dict[str, CallSession] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._reserved: set[str] = set()
        self.results: deque[tuple[str, CallStats]] = deque(maxlen=keep_results)

    @property
    def active(self) -> int:
        return len(self.sessions) + len(self._reserved)

    async def create(self, req: NewCall) -> int:
        """Oturumu açar, RTP portunu döner."""
        if not req.call_id:
            raise BadRequest("call_id boş")
        if req.codec not in CODECS:
            raise BadRequest(f"desteklenmeyen codec: {req.codec} (pcma | pcmu)")
        self._admit(req.call_id)
        self._reserved.add(req.call_id)   # await'ler sırasında aynı call_id ikinci kez açılamasın
        try:
            media, port = await self._open_media(req)
            session = self.session_factory(req, media)
        except BaseException:
            self._reserved.discard(req.call_id)
            raise
        self._reserved.discard(req.call_id)
        self.sessions[req.call_id] = session
        self._tasks[req.call_id] = asyncio.create_task(self._run(req.call_id, session, port),
                                                       name=f"cagri-{req.call_id}")
        log.info("[%s] çağrı açıldı: port %d, %s, aktif %d", req.call_id, port, req.codec, self.active)
        return port

    def _admit(self, call_id: str) -> None:
        if call_id in self.sessions or call_id in self._reserved:
            raise CallExists(f"açık oturum var: {call_id}", call_id)
        if not self.ready():
            raise Unavailable("bağımlı servisler hazır değil")
        if self.active >= self.max_calls:
            raise Unavailable(f"kapasite dolu ({self.max_calls})")

    def attach(self, session: CallSession) -> asyncio.Task:
        """RTP portu gerektirmeyen oturum (arayüzden tarayıcı ile deneme): aynı kapasite ve hazır olma kuralları,
        izlemede diğer çağrılarla birlikte görünür. Dönen görev oturum bitince tamamlanır."""
        self._admit(session.call_id)
        self.sessions[session.call_id] = session
        task = asyncio.create_task(self._run(session.call_id, session, None), name=f"cagri-{session.call_id}")
        self._tasks[session.call_id] = task
        log.info("[%s] çağrı açıldı (%s), aktif %d", session.call_id, session.stats.channel, self.active)
        return task

    async def _open_media(self, req: NewCall) -> tuple[MediaPort, int]:
        for _ in range(self.ports.available):
            port = self.ports.take()
            if port is None:
                break
            try:
                return await self.media_factory(port, req), port
            except OSError as e:
                log.warning("RTP portu %d açılamadı (%s), sıradaki deneniyor", port, e)
                self.ports.give_back(port)
        raise Unavailable("boş RTP portu yok")

    async def _run(self, call_id: str, session: CallSession, port: int | None) -> None:
        try:
            stats = await session.run()
            self.results.append((call_id, stats))
            if self.on_finished is not None:
                try:
                    await self.on_finished(call_id, stats)
                except Exception:
                    log.exception("[%s] çağrı geçmişi yazılamadı", call_id)
        except Exception:
            log.exception("[%s] oturum hatası", call_id)
        finally:
            self.sessions.pop(call_id, None)
            self._tasks.pop(call_id, None)
            if port is not None:
                self.ports.give_back(port)

    def event(self, call_id: str, kind: str, data: dict) -> None:
        s = self.sessions.get(call_id)
        if s is None:
            raise CallNotFound(f"oturum yok: {call_id}", call_id)
        if kind == "transfer_result":
            status = data.get("status")
            if status not in TRANSFER_STATUSES:
                raise BadRequest(f"geçersiz aktarma sonucu: {status} ({' | '.join(TRANSFER_STATUSES)})")
            s.transfer_result(status)
        elif kind == "dtmf":
            digit = str(data.get("digit", ""))
            if len(digit) != 1 or digit not in "0123456789*#":
                raise BadRequest(f"geçersiz DTMF: {digit!r}")
            s.dtmf(digit)
        else:
            raise BadRequest(f"bilinmeyen olay: {kind}")

    async def end(self, call_id: str, reason: str = "hangup") -> None:
        """İdempotent: bilinmeyen çağrı sessizce geçilir (6.2.1)."""
        s, task = self.sessions.get(call_id), self._tasks.get(call_id)
        if s is None:
            return
        s.hangup(reason)
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(asyncio.shield(task), timeout=5)

    async def shutdown(self) -> None:
        for cid in list(self.sessions):
            await self.end(cid, "shutdown")
