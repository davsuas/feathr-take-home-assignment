"""map #4, #12 - FR-027..FR-030, SC-008, SC-009.

Against a real Redis, because TTL expiry and NX-lock semantics are the behaviour under
test and a fake would only prove the fake works.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
import redis.asyncio as redis

from eventplatform.application.realtime_stats import RealtimeStats
from eventplatform.domain.identity import TenantContext
from eventplatform.domain.policies.cache_key import realtime_stats_key
from eventplatform.infrastructure.cache.redis.stats_cache import RedisStatsCache
from eventplatform.infrastructure.persistence.mongo.event_repository import MongoEventRepository
from eventplatform.ports.clock import SystemClock
from tests.conftest import requires_docker
from tests.integration.test_stats import event_at
from tests.unit.fakes import RecordingMetrics

pytestmark = requires_docker

TENANT_A = TenantContext(tenant_id="tenant_a", credential_hash="hash_a")
TENANT_B = TenantContext(tenant_id="tenant_b", credential_hash="hash_b")


@pytest.fixture
async def cache(redis_url):  # type: ignore[no-untyped-def]
    client = redis.from_url(redis_url, decode_responses=True)
    await client.flushdb()
    yield RedisStatsCache(client)
    await client.aclose()


@pytest.fixture
async def realtime(mongo_database, cache):  # type: ignore[no-untyped-def]
    repository = MongoEventRepository(mongo_database)
    now = datetime.now(UTC)
    for index in range(4):
        await repository.insert(TENANT_A, event_at(f"rt_{index}", now, "donation"))
    await repository.insert(TENANT_B, event_at("rt_b", now, "donation", tenant="tenant_b"))
    metrics = RecordingMetrics()
    return (
        RealtimeStats(
            repository=repository,
            cache=cache,
            clock=SystemClock(),
            metrics=metrics,
            ttl_seconds=2,
            lock_ttl_seconds=1,
        ),
        metrics,
        repository,
    )


async def test_cache_miss_then_hit(realtime) -> None:  # type: ignore[no-untyped-def]
    stats, metrics, _ = realtime

    first = await stats(TENANT_A, window="1h")
    assert first.cached is False
    assert first.degraded is False
    assert first.counts == {"donation": 4}

    second = await stats(TENANT_A, window="1h")
    assert second.cached is True
    assert second.counts == {"donation": 4}
    assert metrics.counters["cache_hit_total"] == 1
    assert metrics.counters["cache_miss_total"] == 1


async def test_cached_response_reports_its_age(realtime) -> None:  # type: ignore[no-untyped-def]
    stats, _, _ = realtime
    await stats(TENANT_A, window="1h")
    await asyncio.sleep(1.1)
    second = await stats(TENANT_A, window="1h")
    assert second.cached is True
    assert second.age_seconds >= 1  # FR-029: the caller can judge the staleness


async def test_ttl_expiry_forces_recomputation(realtime) -> None:  # type: ignore[no-untyped-def]
    stats, _, repository = realtime
    assert (await stats(TENANT_A, window="1h")).counts == {"donation": 4}

    await repository.insert(TENANT_A, event_at("rt_new", datetime.now(UTC), "pageview"))
    # Still inside the window, so the stale answer is served - that is the bargain.
    assert (await stats(TENANT_A, window="1h")).counts == {"donation": 4}

    await asyncio.sleep(2.2)
    refreshed = await stats(TENANT_A, window="1h")
    assert refreshed.cached is False
    assert refreshed.counts == {"donation": 4, "pageview": 1}


async def test_two_tenants_never_share_an_entry(realtime, cache) -> None:  # type: ignore[no-untyped-def]
    stats, _, _ = realtime
    assert (await stats(TENANT_A, window="1h")).counts == {"donation": 4}
    assert (await stats(TENANT_B, window="1h")).counts == {"donation": 1}

    assert await cache.get(
        TENANT_A, realtime_stats_key("tenant_a", {"window": "1h", "event_type": None})
    )
    assert await cache.get(
        TENANT_B, realtime_stats_key("tenant_b", {"window": "1h", "event_type": None})
    )


async def test_different_filters_never_share_an_entry(realtime) -> None:  # type: ignore[no-untyped-def]
    stats, _, _ = realtime
    await stats(TENANT_A, window="1h")
    # A different window is a different question and must miss, not serve the 1h answer.
    assert (await stats(TENANT_A, window="24h")).cached is False


async def test_cache_unavailable_degrades(mongo_database) -> None:  # type: ignore[no-untyped-def]
    """map #12 - SC-009: the endpoint still answers, marked degraded."""
    repository = MongoEventRepository(mongo_database)
    now = datetime.now(UTC)
    for index in range(3):
        await repository.insert(TENANT_A, event_at(f"deg_{index}", now, "donation"))

    dead_client = redis.from_url("redis://127.0.0.1:16399/0", socket_connect_timeout=1)
    metrics = RecordingMetrics()
    stats = RealtimeStats(
        repository=repository,
        cache=RedisStatsCache(dead_client),
        clock=SystemClock(),
        metrics=metrics,
        ttl_seconds=30,
        lock_ttl_seconds=5,
    )

    summary = await stats(TENANT_A, window="1h")
    assert summary.degraded is True
    assert summary.counts == {"donation": 3}  # correct answer, just slower
    assert metrics.counters["cache_error_total"] == 1
    assert metrics.counters["cache_fallback_total"] == 1
    await dead_client.aclose()


async def test_cache_rejects_a_key_missing_its_tenant_segment(cache) -> None:  # type: ignore[no-untyped-def]
    """Defence in depth: a key that lost its tenant segment must fail loudly, not serve."""
    with pytest.raises(ValueError, match="not scoped"):
        await cache.get(TENANT_A, "v1:stats:rt:nope")


async def test_single_flight_lock_admits_one_holder(cache) -> None:  # type: ignore[no-untyped-def]
    key = f"v1:{TENANT_A.tenant_id}:lock:stats:abc"
    results = await asyncio.gather(*(cache.acquire_lock(TENANT_A, key, ttl=5) for _ in range(5)))
    assert results.count(True) == 1
