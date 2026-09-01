"""Live summary: cache-aside with bounded staleness (FR-027..FR-030).

Three behaviours are deliberate:

- No write-path invalidation. Under a write-heavy event stream, invalidating on write
  means invalidating continuously, which is a cache that never serves. Bounded staleness
  is the honest trade.
- Single-flight on miss. The characteristic failure of cache-aside under load is the
  stampede at expiry, not the miss itself.
- A cache error is never fatal. Redis is a derived store; losing it costs latency, not
  correctness.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from eventplatform.domain.errors import CacheUnavailable
from eventplatform.domain.identity import TenantContext
from eventplatform.domain.policies.cache_key import realtime_stats_key, single_flight_key
from eventplatform.domain.policies.time_window import AppliedRange
from eventplatform.ports.clock import Clock
from eventplatform.ports.event_repository import EventRepository
from eventplatform.ports.metrics import Metrics
from eventplatform.ports.stats_cache import StatsCache

WINDOWS: dict[str, timedelta] = {
    "5m": timedelta(minutes=5),
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
}


@dataclass(frozen=True, slots=True)
class RealtimeSummary:
    counts: dict[str, int]
    cached: bool
    age_seconds: int
    degraded: bool


class RealtimeStats:
    def __init__(
        self,
        *,
        repository: EventRepository,
        cache: StatsCache,
        clock: Clock,
        metrics: Metrics,
        ttl_seconds: int,
        lock_ttl_seconds: int,
    ) -> None:
        self._repository = repository
        self._cache = cache
        self._clock = clock
        self._metrics = metrics
        self._ttl = ttl_seconds
        self._lock_ttl = lock_ttl_seconds

    async def __call__(
        self, tenant: TenantContext, *, window: str = "1h", event_type: str | None = None
    ) -> RealtimeSummary:
        filters: dict[str, Any] = {"window": window, "event_type": event_type}
        key = realtime_stats_key(tenant.tenant_id, filters)

        try:
            hit = await self._cache.get(tenant, key)
        except CacheUnavailable:
            self._metrics.increment("cache_error_total")
            return await self._degraded(tenant, window, event_type)

        if hit is not None:
            self._metrics.increment("cache_hit_total")
            return RealtimeSummary(
                counts={k: int(v) for k, v in hit.value.items()},
                cached=True,
                age_seconds=hit.age_seconds,
                degraded=False,
            )

        self._metrics.increment("cache_miss_total")
        counts = await self._compute(tenant, window, event_type)

        try:
            # Best-effort. Losing the write costs a recomputation, never correctness,
            # so a cache failure here must not fail a request that already has its answer.
            if await self._cache.acquire_lock(
                tenant, single_flight_key(tenant.tenant_id, filters), ttl=self._lock_ttl
            ):
                await self._cache.set(tenant, key, dict(counts), ttl=self._ttl)
        except CacheUnavailable:
            self._metrics.increment("cache_error_total")

        return RealtimeSummary(counts=counts, cached=False, age_seconds=0, degraded=False)

    async def _degraded(
        self, tenant: TenantContext, window: str, event_type: str | None
    ) -> RealtimeSummary:
        self._metrics.increment("cache_fallback_total")
        counts = await self._compute(tenant, window, event_type)
        return RealtimeSummary(counts=counts, cached=False, age_seconds=0, degraded=True)

    async def _compute(
        self, tenant: TenantContext, window: str, event_type: str | None
    ) -> dict[str, int]:
        now = self._clock.now()
        applied = AppliedRange(
            start=now - WINDOWS.get(window, WINDOWS["1h"]), end=now, clamped=False
        )
        return await self._repository.count_by_type(tenant, applied, event_type=event_type)
