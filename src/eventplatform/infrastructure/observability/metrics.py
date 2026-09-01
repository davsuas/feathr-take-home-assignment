"""Prometheus metrics adapter (constitution VI.4).

Label cardinality is bounded deliberately. ``tenant_id`` appears only on ingest counters,
where the tenant count is small and known; putting it on per-event processing counters
would multiply every series by the customer count and eventually take out Prometheus.
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

_COUNTERS: dict[str, tuple[str, tuple[str, ...]]] = {
    "events_accepted_total": ("Events validated and queued", ("tenant_id", "event_type")),
    "events_rejected_validation_total": ("Submissions failing validation", ("field",)),
    "ingest_rejected_backpressure_total": ("Submissions refused, queue at capacity", ()),
    "ingest_rejected_rate_limit_total": ("Submissions refused by the rate limiter", ()),
    "processing_attempts_total": ("Worker processing attempts", ()),
    "processing_retries_total": ("Retries scheduled after a failure", ()),
    "duplicate_suppressed_total": ("Redeliveries suppressed by idempotency", ()),
    "duplicate_conflict_total": ("Same id, different content - first kept", ()),
    "dead_letter_total": ("Events that exhausted every attempt", ()),
    "mongo_write_failures_total": ("Canonical write failures", ()),
    "projection_failures_total": ("Search projection failures", ()),
    "projection_reconciled_total": ("Projections repaired by the reconciler", ()),
    "cache_hit_total": ("Live summaries served from cache", ()),
    "cache_miss_total": ("Live summaries recomputed", ()),
    "cache_error_total": ("Cache errors encountered", ()),
    "cache_fallback_total": ("Live summaries computed because the cache was down", ()),
    "search_unavailable_total": ("Search requests refused, index unreachable", ()),
}

_GAUGES: dict[str, str] = {
    "queue_depth": "Messages waiting in the queue",
    "projection_lag_seconds": "Age of the oldest pending projection",
}

_HISTOGRAMS: dict[str, tuple[str, tuple[str, ...]]] = {
    "http_request_duration_seconds": ("Request duration", ("route",)),
    "processing_duration_seconds": ("Message processing duration", ()),
}


class PrometheusMetrics:
    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()
        self._counters = {
            name: Counter(name, doc, labels, registry=self.registry)
            for name, (doc, labels) in _COUNTERS.items()
        }
        self._gauges = {
            name: Gauge(name, doc, registry=self.registry) for name, doc in _GAUGES.items()
        }
        self._histograms = {
            name: Histogram(name, doc, labels, registry=self.registry)
            for name, (doc, labels) in _HISTOGRAMS.items()
        }

    def increment(self, name: str, value: int = 1, **labels: str) -> None:
        counter = self._counters.get(name)
        if counter is None:
            return
        (counter.labels(**labels) if labels else counter).inc(value)

    def observe(self, name: str, value: float, **labels: str) -> None:
        histogram = self._histograms.get(name)
        if histogram is None:
            return
        (histogram.labels(**labels) if labels else histogram).observe(value)

    def gauge(self, name: str, value: float, **labels: str) -> None:
        gauge = self._gauges.get(name)
        if gauge is not None:
            gauge.set(value)

    def render(self) -> bytes:
        return generate_latest(self.registry)
