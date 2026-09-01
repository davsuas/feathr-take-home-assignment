"""map #1 - ingest -> queue -> worker -> MongoDB -> query (SC-002).

The full P1 journey against a real MongoDB.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from eventplatform.application.ingest_event import IngestEvent
from eventplatform.application.process_event import ProcessEvent
from eventplatform.application.query_events import QueryEvents
from eventplatform.domain.identity import TenantContext
from eventplatform.domain.policies.retry import RetryPolicy
from eventplatform.domain.policies.time_window import TimeWindowPolicy
from eventplatform.infrastructure.observability.correlation import new_id
from eventplatform.infrastructure.persistence.mongo.dead_letter_store import MongoDeadLetterStore
from eventplatform.infrastructure.persistence.mongo.event_repository import MongoEventRepository
from eventplatform.infrastructure.queue.in_memory import InMemoryQueue
from eventplatform.ports.clock import SystemClock
from eventplatform.worker.consumer import Worker
from tests.conftest import requires_docker
from tests.unit.fakes import RecordingMetrics

pytestmark = requires_docker

TENANT_A = TenantContext(tenant_id="tenant_a", credential_hash="hash_a")
TENANT_B = TenantContext(tenant_id="tenant_b", credential_hash="hash_b")


def submission(event_id: str | None = None, **overrides: object) -> dict[str, object]:
    now = datetime.now(UTC)
    base: dict[str, object] = {
        "event_type": "donation",
        "occurred_at": (now - timedelta(minutes=5)).isoformat(),
        "contact_id": "contact_42",
        "campaign_id": "spring_appeal",
        "channel": "email",
        "source_url": "https://example.org/give",
        "conversion": {"goal_id": "gift", "value": 250.0, "currency": "USD"},
        "consent": {"analytics": True, "advertising": False},
        "metadata": {"browser": "Safari", "device_type": "mobile"},
    }
    if event_id:
        base["event_id"] = event_id
    return {**base, **overrides}


@pytest.fixture
async def pipeline(mongo_database):  # type: ignore[no-untyped-def]
    repository = MongoEventRepository(mongo_database)
    queue = InMemoryQueue(capacity=1000, visibility_timeout=60)
    metrics = RecordingMetrics()
    clock = SystemClock()
    ingest = IngestEvent(
        queue=queue,
        clock=clock,
        metrics=metrics,
        publish_timeout_seconds=2.0,
        id_factory=new_id,
    )
    worker = Worker(
        queue=queue,
        process=ProcessEvent(
            repository=repository, search_index=None, metrics=metrics, clock=clock
        ),
        dead_letters=MongoDeadLetterStore(mongo_database),
        retry_policy=RetryPolicy(max_attempts=3, base_seconds=0.001, cap_seconds=0.002),
        clock=clock,
        metrics=metrics,
    )
    query = QueryEvents(repository=repository, time_policy=TimeWindowPolicy(), max_page_size=200)
    return ingest, worker, query, repository, metrics


async def test_ingest_to_query_roundtrip(pipeline) -> None:  # type: ignore[no-untyped-def]
    ingest, worker, query, _, _ = pipeline

    receipt = await ingest(TENANT_A, submission("evt_roundtrip"))
    assert receipt.status == "accepted"
    assert receipt.event_id_origin == "sender"

    await worker.drain()

    now = datetime.now(UTC)
    page = await query(TENANT_A, start=now - timedelta(days=1), end=now + timedelta(minutes=1))
    assert len(page.items) == 1
    stored = page.items[0]
    assert stored.event_id == "evt_roundtrip"
    assert stored.identity.contact_id == "contact_42"
    assert stored.attribution.conversion is not None
    assert stored.attribution.conversion.value == 250.0
    assert stored.consent.analytics is True
    assert stored.metadata == {"browser": "Safari", "device_type": "mobile"}


async def test_platform_generates_an_id_when_the_sender_omits_one(pipeline) -> None:  # type: ignore[no-untyped-def]
    ingest, worker, query, _, _ = pipeline
    receipt = await ingest(TENANT_A, submission())
    # The response says which path was taken, so the sender knows their retry will NOT
    # be deduplicated (spec D2).
    assert receipt.event_id_origin == "platform"
    assert receipt.event_id.startswith("evt_")
    await worker.drain()
    now = datetime.now(UTC)
    page = await query(TENANT_A, start=now - timedelta(days=1), end=now + timedelta(minutes=1))
    assert page.items[0].event_id == receipt.event_id


async def test_filters_narrow_the_result(pipeline) -> None:  # type: ignore[no-untyped-def]
    ingest, worker, query, _, _ = pipeline
    await ingest(TENANT_A, submission("evt_a", event_type="donation"))
    await ingest(TENANT_A, submission("evt_b", event_type="pageview"))
    await worker.drain()

    now = datetime.now(UTC)
    window = {"start": now - timedelta(days=1), "end": now + timedelta(minutes=1)}
    donations = await query(TENANT_A, **window, event_type="donation")
    assert [e.event_id for e in donations.items] == ["evt_a"]

    by_contact = await query(TENANT_A, **window, contact_id="contact_42")
    assert len(by_contact.items) == 2

    by_url = await query(TENANT_A, **window, source_url="https://example.org/give")
    assert len(by_url.items) == 2


async def test_accepted_equals_stored_plus_dead_lettered(pipeline) -> None:  # type: ignore[no-untyped-def]
    """SC-002: no event is unaccounted for."""
    ingest, worker, _, repository, _ = pipeline
    for index in range(25):
        await ingest(TENANT_A, submission(f"evt_{index}"))
    await worker.drain()

    now = datetime.now(UTC)
    page = await repository.find_page(
        TENANT_A,
        __import__("eventplatform.ports.event_repository", fromlist=["EventFilters"]).EventFilters(
            start=now - timedelta(days=1), end=now + timedelta(minutes=1)
        ),
        cursor=None,
        limit=100,
    )
    assert len(page.items) == 25
