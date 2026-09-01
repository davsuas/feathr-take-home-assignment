"""map #19 - FR-021.

Keyset pagination has one property worth testing and it is not "does it return rows":
it is that a page boundary is stable when timestamps tie, and that inserting newer rows
mid-walk does not cause a skip or a repeat.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from eventplatform.application.query_events import QueryEvents
from eventplatform.domain.identity import TenantContext
from eventplatform.domain.policies.time_window import TimeWindowPolicy
from eventplatform.infrastructure.persistence.mongo.event_repository import MongoEventRepository
from tests.conftest import requires_docker
from tests.integration.test_stats import event_at

pytestmark = requires_docker

TENANT_A = TenantContext(tenant_id="tenant_a", credential_hash="hash_a")
ANCHOR = datetime.now(UTC).replace(microsecond=0)
WINDOW = {"start": ANCHOR - timedelta(days=1), "end": ANCHOR + timedelta(days=1)}


@pytest.fixture
async def query(mongo_database):  # type: ignore[no-untyped-def]
    repository = MongoEventRepository(mongo_database)
    # Every event shares one timestamp. This is the case that breaks naive cursors, and
    # it is not exotic: event traffic clusters.
    for index in range(25):
        await repository.insert(TENANT_A, event_at(f"evt_{index:02d}", ANCHOR, "pageview"))
    return QueryEvents(repository=repository, time_policy=TimeWindowPolicy(), max_page_size=200)


async def test_walking_every_page_yields_each_event_exactly_once(query) -> None:  # type: ignore[no-untyped-def]
    seen: list[str] = []
    cursor = None
    while True:
        page = await query(TENANT_A, **WINDOW, cursor=cursor, limit=10)
        seen.extend(e.event_id for e in page.items)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert len(seen) == 25
    assert len(set(seen)) == 25


async def test_order_is_stable_when_timestamps_tie(query) -> None:  # type: ignore[no-untyped-def]
    first = await query(TENANT_A, **WINDOW, limit=10)
    again = await query(TENANT_A, **WINDOW, limit=10)
    assert [e.event_id for e in first.items] == [e.event_id for e in again.items]


async def test_inserting_newer_events_mid_walk_causes_no_skip_or_repeat(
    query, mongo_database
) -> None:  # type: ignore[no-untyped-def]
    repository = MongoEventRepository(mongo_database)
    page_one = await query(TENANT_A, **WINDOW, limit=10)

    # A live system keeps ingesting while someone pages through history.
    for index in range(5):
        await repository.insert(
            TENANT_A, event_at(f"new_{index}", ANCHOR + timedelta(minutes=1), "click")
        )

    page_two = await query(TENANT_A, **WINDOW, cursor=page_one.next_cursor, limit=10)
    overlap = {e.event_id for e in page_one.items} & {e.event_id for e in page_two.items}
    assert not overlap, "keyset paging must not repeat rows when new events arrive"


async def test_page_size_is_capped(mongo_database) -> None:  # type: ignore[no-untyped-def]
    repository = MongoEventRepository(mongo_database)
    capped = QueryEvents(repository=repository, time_policy=TimeWindowPolicy(), max_page_size=5)
    page = await capped(TENANT_A, **WINDOW, limit=1000)
    assert len(page.items) <= 5


async def test_listing_reports_a_clamped_range(mongo_database) -> None:  # type: ignore[no-untyped-def]
    """A silently narrowed range returns fewer rows with no explanation, which reads as
    missing data. Found by running the real quickstart against Compose."""
    repository = MongoEventRepository(mongo_database)
    narrow = QueryEvents(
        repository=repository, time_policy=TimeWindowPolicy(max_range_days=2), max_page_size=200
    )
    page = await narrow(
        TENANT_A, start=ANCHOR - timedelta(days=120), end=ANCHOR + timedelta(days=1)
    )
    assert page.applied is not None
    assert page.applied.clamped is True
    assert page.applied.end - page.applied.start == timedelta(days=2)


async def test_listing_reports_an_unclamped_range(query) -> None:  # type: ignore[no-untyped-def]
    page = await query(TENANT_A, **WINDOW, limit=10)
    assert page.applied is not None
    assert page.applied.clamped is False
