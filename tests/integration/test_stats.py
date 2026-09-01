"""map #2 - SC-007, FR-022, FR-023.

Aggregation against a real MongoDB, because $dateTrunc bucketing is the behaviour under
test and a fake would just re-implement it (and agree with itself).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from eventplatform.application.aggregate_stats import AggregateStats
from eventplatform.domain.event import Event
from eventplatform.domain.identity import TenantContext
from eventplatform.domain.policies.time_window import (
    Bucket,
    TimeWindowPolicy,
    bucket_starts,
)
from eventplatform.infrastructure.persistence.mongo.event_repository import MongoEventRepository
from tests.conftest import requires_docker

pytestmark = requires_docker

TENANT_A = TenantContext(tenant_id="tenant_a", credential_hash="hash_a")
TENANT_B = TenantContext(tenant_id="tenant_b", credential_hash="hash_b")
ANCHOR = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)


def event_at(event_id: str, when: datetime, event_type: str, tenant: str = "tenant_a") -> Event:
    return Event(
        tenant_id=tenant,
        event_id=event_id,
        event_type=event_type,
        occurred_at=when,
        received_at=datetime.now(UTC),
    )


@pytest.fixture
async def seeded(mongo_database):  # type: ignore[no-untyped-def]
    repository = MongoEventRepository(mongo_database)
    # 3 donations and 2 pageviews today; 1 donation two days ago. Plus a decoy tenant.
    for index in range(3):
        await repository.insert(TENANT_A, event_at(f"d_{index}", ANCHOR, "donation"))
    for index in range(2):
        await repository.insert(TENANT_A, event_at(f"p_{index}", ANCHOR, "pageview"))
    await repository.insert(TENANT_A, event_at("d_old", ANCHOR - timedelta(days=2), "donation"))
    await repository.insert(TENANT_B, event_at("b_1", ANCHOR, "donation", tenant="tenant_b"))
    return repository


@pytest.fixture
def stats(seeded):  # type: ignore[no-untyped-def]
    return AggregateStats(repository=seeded, time_policy=TimeWindowPolicy())


WINDOW = {"start": ANCHOR - timedelta(days=5), "end": ANCHOR + timedelta(days=1)}


async def test_ingest_to_stats_roundtrip(stats) -> None:  # type: ignore[no-untyped-def]
    result = await stats(TENANT_A, **WINDOW, bucket=Bucket.DAY)
    counted = {(b.bucket_start.date(), b.event_type): b.count for b in result.buckets if b.count}
    assert counted[(ANCHOR.date(), "donation")] == 3
    assert counted[(ANCHOR.date(), "pageview")] == 2
    assert counted[((ANCHOR - timedelta(days=2)).date(), "donation")] == 1


async def test_totals_are_identical_across_bucket_sizes(stats) -> None:  # type: ignore[no-untyped-def]
    """US3 scenario 2: changing the grouping must not change the numbers."""
    totals = {}
    for bucket in (Bucket.HOUR, Bucket.DAY, Bucket.WEEK):
        result = await stats(TENANT_A, **WINDOW, bucket=bucket)
        totals[bucket] = sum(b.count for b in result.buckets)
    assert totals[Bucket.HOUR] == totals[Bucket.DAY] == totals[Bucket.WEEK] == 6


async def test_empty_buckets_are_zero_filled_not_omitted(stats) -> None:  # type: ignore[no-untyped-def]
    """US3 scenario 4: an omitted bucket reads as 'no data'; a zero reads as 'nothing
    happened'. Those are different claims."""
    result = await stats(TENANT_A, **WINDOW, bucket=Bucket.DAY)
    zero_buckets = [b for b in result.buckets if b.count == 0]
    assert zero_buckets, "expected explicit zeros for days with no events"

    # Every day boundary the range touches must be present, not only the days that
    # happen to have events.
    expected = set(bucket_starts(result.applied, Bucket.DAY))
    assert {b.bucket_start for b in result.buckets} == expected


async def test_range_wider_than_the_maximum_is_clamped_and_says_so(seeded) -> None:  # type: ignore[no-untyped-def]
    stats = AggregateStats(repository=seeded, time_policy=TimeWindowPolicy(max_range_days=2))
    result = await stats(
        TENANT_A,
        start=ANCHOR - timedelta(days=90),
        end=ANCHOR + timedelta(days=1),
        bucket=Bucket.DAY,
    )
    assert result.applied.clamped
    assert result.applied.end - result.applied.start == timedelta(days=2)


async def test_event_type_filter_narrows_the_aggregate(stats) -> None:  # type: ignore[no-untyped-def]
    result = await stats(TENANT_A, **WINDOW, bucket=Bucket.DAY, event_type="donation")
    assert sum(b.count for b in result.buckets) == 4
    assert {b.event_type for b in result.buckets} == {"donation"}
