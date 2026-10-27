"""Devre kesici (circuit breaker, tasarim.md 9.2).

Bir bağımlı servis (LLM, TTS, STT) art arda `failures` kez hata verirse devre **açılır**: `reset_s` boyunca o servis hiç
çağrılmaz, istek anında hata alır. Böylece her çağrı ayrı ayrı zaman aşımını (LLM 5 sn, TTS 3 sn) beklemez; arayan
beklemeden hata anonsu + operatöre yönlenir, yeni çağrılar da sağlık kontrolünde reddedilir (santral operatöre
yönlendirir). Süre dolunca devre **yarı açık** olur: bir deneme çağrısına izin verilir; başarılıysa kapanır,
başarısızsa yeniden açılır.

Sarmalayıcılar (`BreakerLlm`, `BreakerTts`, `BreakerStt`) portların aynısını uygular; çekirdek bunu fark etmez.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from .ports import Audio, LlmError, LlmMessage, SttError, TtsError

log = logging.getLogger(__name__)


def supports_stream(llm: Any) -> bool:
    """LLM akışlı cevap veriyor mu (sarmalayıcılar içteki adaptöre bakar)."""
    flag = getattr(llm, "supports_stream", None)
    return bool(flag) if flag is not None else callable(getattr(llm, "chat_stream", None))


class CircuitBreaker:
    def __init__(self, name: str, failures: int = 3, reset_s: float = 30.0,
                 clock: Callable[[], float] = time.monotonic):
        self.name, self.threshold, self.reset_s, self.clock = name, failures, reset_s, clock
        self.failures = 0
        self.opened_at: float | None = None
        self.trips = 0

    @property
    def is_open(self) -> bool:
        """Soğuma süresi içinde mi (yarı açıkken False: deneme yapılabilir)."""
        return self.opened_at is not None and self.clock() - self.opened_at < self.reset_s

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        return "open" if self.is_open else "half_open"

    def allow(self) -> bool:
        return not self.is_open

    def success(self) -> None:
        if self.opened_at is not None:
            log.info("devre kapandı: %s (servis yeniden cevap veriyor)", self.name)
        self.failures, self.opened_at = 0, None

    def failure(self) -> None:
        self.failures += 1
        half_open = self.opened_at is not None
        if half_open or self.failures >= self.threshold:
            self.opened_at = self.clock()
            self.trips += 1
            log.error("devre AÇILDI: %s (%d art arda hata); %.0f sn boyunca çağrılmayacak", self.name,
                      self.failures, self.reset_s)


class BreakerLlm:
    def __init__(self, inner: Any, breaker: CircuitBreaker):
        self.inner, self.breaker = inner, breaker

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def chat(self, messages, tools) -> LlmMessage:
        if not self.breaker.allow():
            raise LlmError(f"devre açık: {self.breaker.name}")
        try:
            out = await self.inner.chat(messages, tools)
        except (LlmError, asyncio.CancelledError, TimeoutError):   # iptal = diyalogun zaman aşımı
            self.breaker.failure()
            raise
        self.breaker.success()
        return out

    async def chat_stream(self, messages, tools):
        if not self.breaker.allow():
            raise LlmError(f"devre açık: {self.breaker.name}")
        try:
            async for d in self.inner.chat_stream(messages, tools):
                yield d
        except (LlmError, asyncio.CancelledError, TimeoutError):
            self.breaker.failure()
            raise
        self.breaker.success()

    @property
    def supports_stream(self) -> bool:
        return supports_stream(self.inner)


class BreakerTts:
    def __init__(self, inner: Any, breaker: CircuitBreaker):
        self.inner, self.breaker = inner, breaker

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def synthesize(self, text: str) -> Audio:
        if not self.breaker.allow():
            raise TtsError(f"devre açık: {self.breaker.name}")
        try:
            out = await self.inner.synthesize(text)
        except (TtsError, TimeoutError):
            self.breaker.failure()
            raise
        except asyncio.CancelledError:
            raise            # söze girme: hata değil
        self.breaker.success()
        return out


class BreakerStt:
    def __init__(self, inner: Any, breaker: CircuitBreaker):
        self.inner, self.breaker = inner, breaker

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def open(self, call_id: str, codec: str, hotwords: list[str]):
        if not self.breaker.allow():
            raise SttError(f"devre açık: {self.breaker.name}")
        try:
            out = await self.inner.open(call_id, codec, hotwords)
        except SttError:
            self.breaker.failure()
            raise
        self.breaker.success()
        return out
