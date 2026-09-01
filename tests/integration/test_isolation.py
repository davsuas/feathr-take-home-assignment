"""map #9 - SC-005, the tenant isolation probe.

One file, grown by each story: listing, aggregation, search, and cache are all probed
here rather than in four disconnected places, because isolation is a single property of
the system and a gap in any one path is a breach of all of it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from eventplatform.application.aggregate_stats import AggregateStats
from eventplatform.application.query_events import QueryEvents
from eventplatform.application.realtime_stats import RealtimeStats
from eventplatform.domain.identity import TenantContext
from eventplatform.domain.policies.cache_key import realtime_stats_key
from eventplatform.domain.policies.time_window import Bucket, TimeWindowPolicy
from eventplatform.infrastructure.persistence.mongo.event_repository import MongoEventRepository
from eventplatform.ports.clock import SystemClock
from tests.conftest import requires_docker
from tests.integration.test_idempotency import make_event
from tests.unit.fakes import FakeStatsCache, RecordingMetrics

pytestmark = requires_docker

TENANT_A = TenantContext(tenant_id="tenant_a", credential_hash="hash_a")
TENANT_B = TenantContext(tenant_id="tenant_b", credential_hash="hash_b")
WINDOW = {
    "start": datetime.now(UTC) - timedelta(days=1),
    "end": datetime.now(UTC) + timedelta(minutes=5),
}


@pytest.fixture
async def seeded(mongo_database):  # type: ignore[no-untyped-def]
    repository = MongoEventRepository(mongo_database)
    for index in range(5):
        await repository.insert(TENANT_A, make_event(f"evt_a_{index}"))
    for index in range(7):
        await repository.insert(TENANT_B, make_event(f"evt_b_{index}", tenant="tenant_b"))
    return repository


async def test_tenant_a_cannot_read_tenant_b_events(seeded) -> None:  # type: ignore[no-untyped-def]
    query = QueryEvents(repository=seeded, time_policy=TimeWindowPolicy(), max_page_size=200)
    page = await query(TENANT_A, **WINDOW, limit=200)
    assert len(page.items) == 5
    assert all(e.tenant_id == "tenant_a" for e in page.items)


async def test_tenant_a_cannot_aggregate_tenant_b_events(seeded) -> None:  # type: ignore[no-untyped-def]
    stats = AggregateStats(repository=seeded, time_policy=TimeWindowPolicy())
    result = await stats(TENANT_A, **WINDOW, bucket=Bucket.DAY)
    assert sum(b.count for b in result.buckets) == 5


async def test_foreign_event_id_is_indistinguishable_from_a_missing_one(seeded) -> None:  # type: ignore[no-untyped-def]
    """FR-034: existence must not be probeable across tenants."""
    foreign = await seeded.get(TENANT_A, "evt_b_0")
    missing = await seeded.get(TENANT_A, "evt_does_not_exist")
    assert foreign is None
    assert foreign == missing


async def test_two_tenants_with_identical_filters_never_share_a_cache_entry(seeded) -> None:  # type: ignore[no-untyped-def]
    """FR-028 end to end, not merely at the key-construction level."""
    cache = FakeStatsCache()
    metrics = RecordingMetrics()
    realtime = RealtimeStats(
        repository=seeded,
        cache=cache,
        clock=SystemClock(),
        metrics=metrics,
        ttl_seconds=30,
        lock_ttl_seconds=5,
    )
    a = await realtime(TENANT_A, window="24h")
    b = await realtime(TENANT_B, window="24h")

    assert sum(a.counts.values()) == 5
    assert sum(b.counts.values()) == 7
    assert realtime_stats_key("tenant_a", {"window": "24h", "event_type": None}) in cache.store
    assert realtime_stats_key("tenant_b", {"window": "24h", "event_type": None}) in cache.store
    assert len(cache.store) == 2
