"""Gateway metrikleri (Prometheus, tasarim.md 12.2) — `/metrics`.

Çekirdek metrik kütüphanesini bilmez: tur ölçümleri çağrı oturumunun gözlemcisinden (observer), LLM/TTS süreleri
sarmalayıcılardan, anlık değerler (aktif çağrı, rehber yaşı, bileşen durumu, reddedilen aktarma, RTP sayaçları)
toplama anında çalışma zamanından okunur.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from prometheus_client import CollectorRegistry, Counter, Histogram
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

LATENCY = (0.1, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 7.5, 10.0)


class GatewayMetrics:
    def __init__(self, runtime: Any | None = None):
        self.registry = CollectorRegistry()
        r = self.registry
        self.calls = Counter("calls_total", "Biten çağrılar", ["outcome", "channel"], registry=r)
        self.turn_latency = Histogram("turn_latency_seconds", "Arayanın metni → asistanın ilk sesi", ["channel"],
                                      buckets=LATENCY, registry=r)
        self.dialog_latency = Histogram("dialog_latency_seconds", "Diyalog (LLM + rehber) süresi", buckets=LATENCY,
                                        registry=r)
        self.llm_latency = Histogram("llm_latency_seconds", "LLM çağrısı süresi", buckets=LATENCY, registry=r)
        self.llm_errors = Counter("llm_errors_total", "LLM hata / zaman aşımı", registry=r)
        self.tts_latency = Histogram("tts_latency_seconds", "Cümle başına seslendirme süresi", buckets=LATENCY,
                                     registry=r)
        self.tts_errors = Counter("tts_errors_total", "TTS hatası", registry=r)
        self.actions = Counter("actions_total", "Santral eylemleri", ["type"], registry=r)
        self.barge_in = Counter("barge_in_total", "Söze girme", registry=r)
        self.not_understood = Counter("not_understood_total", "Anlaşılamayan arayan cümlesi", registry=r)
        self.thinking_sound = Counter("thinking_sound_total", "Düşünme sesi çalınan turlar", registry=r)
        if runtime is not None:
            r.register(_RuntimeCollector(runtime))

    def observer(self, channel: str) -> Callable[[str, dict[str, Any]], None]:
        """Çağrı oturumu olaylarını metriğe çevirir."""
        def on(kind: str, data: dict[str, Any]) -> None:
            if kind == "turn":
                if data.get("final_to_audio_s") is not None:
                    self.turn_latency.labels(channel).observe(data["final_to_audio_s"])
                if data.get("dialog_s"):
                    self.dialog_latency.observe(data["dialog_s"])
            elif kind == "heard" and not data.get("text"):
                self.not_understood.inc()
            elif kind == "barge_in":
                self.barge_in.inc()
            elif kind == "action":
                self.actions.labels(data.get("action", "?")).inc()
            elif kind == "thinking_sound":
                self.thinking_sound.inc()
            elif kind == "ended":
                self.calls.labels(data.get("outcome") or "hangup", channel).inc()
        return on


class _RuntimeCollector:
    def __init__(self, rt: Any):
        self.rt = rt

    def collect(self):
        rt = self.rt
        g = GaugeMetricFamily("calls_active", "Açık çağrı")
        g.add_metric([], rt.manager.active)
        yield g
        g = GaugeMetricFamily("calls_capacity", "Eşzamanlı çağrı sınırı")
        g.add_metric([], rt.s.max_concurrent_calls)
        yield g
        age = rt.directory.age_s
        g = GaugeMetricFamily("directory_age_seconds", "Rehber kopyasının yaşı")
        g.add_metric([], age if age is not None else float("nan"))
        yield g
        g = GaugeMetricFamily("directory_entries", "Aranabilir rehber kaydı")
        g.add_metric([], len(rt.directory.index))
        yield g
        g = GaugeMetricFamily("dependency_up", "Bağımlı servis çalışıyor mu", labels=["service"])
        for name, st in rt.components.items():
            g.add_metric([name], 1.0 if st == "ok" else 0.0)
        yield g
        breakers = getattr(rt, "breakers", {})
        g = GaugeMetricFamily("circuit_open", "Devre kesici açık mı", labels=["service"])
        for name, b in breakers.items():
            g.add_metric([name], 1.0 if b.is_open else 0.0)
        yield g
        c = CounterMetricFamily("transfer_policy_rejections", "Güvenlik kuralının reddettiği aktarmalar")
        c.add_metric([], rt.agent.tools.rejections)
        yield c
        totals: dict[str, int] = {}
        for _, st in rt.manager.results:
            for k, v in (st.rtp or {}).items():
                totals[k] = totals.get(k, 0) + int(v)
        c = CounterMetricFamily("rtp_packets", "RTP paket sayaçları (biten son çağrılar)", labels=["kind"])
        for k, v in sorted(totals.items()):
            c.add_metric([k], v)
        yield c


class TimedLlm:
    """LLM sarmalayıcı: süre ve hata metriği."""

    def __init__(self, inner: Any, m: GatewayMetrics):
        self.inner, self.m = inner, m

    def __getattr__(self, name: str) -> Any:     # warmup, aclose, http …
        return getattr(self.inner, name)

    async def chat(self, messages, tools):
        t0 = time.perf_counter()
        try:
            return await self.inner.chat(messages, tools)
        except Exception:
            self.m.llm_errors.inc()
            raise
        finally:
            self.m.llm_latency.observe(time.perf_counter() - t0)


class TimedTts:
    def __init__(self, inner: Any, m: GatewayMetrics):
        self.inner, self.m = inner, m

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def synthesize(self, text: str):
        t0 = time.perf_counter()
        try:
            return await self.inner.synthesize(text)
        except Exception:
            self.m.tts_errors.inc()
            raise
        finally:
            self.m.tts_latency.observe(time.perf_counter() - t0)
