"""map #7, #8 - SC-004, FR-017, FR-018.

Dead-lettering and replay against a real store, so the payload really does survive a
round trip through MongoDB intact.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from eventplatform.application.process_event import ProcessEvent
from eventplatform.application.replay_dead_letter import ReplayDeadLetter
from eventplatform.domain.errors import DeadLetterNotFound
from eventplatform.domain.identity import TenantContext
from eventplatform.domain.policies.retry import RetryPolicy
from eventplatform.infrastructure.observability.correlation import new_id
from eventplatform.infrastructure.persistence.mongo.dead_letter_store import MongoDeadLetterStore
from eventplatform.infrastructure.queue.in_memory import InMemoryQueue
from eventplatform.ports.clock import SystemClock
from eventplatform.ports.event_queue import QueueMessage
from eventplatform.worker.consumer import Worker
from tests.conftest import requires_docker
from tests.unit.fakes import FakeRepository, RecordingMetrics

pytestmark = requires_docker

TENANT_A = TenantContext(tenant_id="tenant_a", credential_hash="hash_a")
TENANT_B = TenantContext(tenant_id="tenant_b", credential_hash="hash_b")
OCCURRED_AT = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=1)


def payload(event_id: str = "evt_dlq") -> dict[str, object]:
    return {
        "event_id": event_id,
        "event_type": "donation",
        "occurred_at": OCCURRED_AT.isoformat(),
        "received_at": datetime.now(UTC).isoformat(),
        "metadata": {"browser": "Safari", "device_type": "mobile"},
        "contact_id": "contact_42",
    }


def message(event_id: str = "evt_dlq", *, attempt: int = 1) -> QueueMessage:
    return QueueMessage(
        message_id=new_id("msg"),
        tenant_id="tenant_a",
        event_id=event_id,
        payload=payload(event_id),
        enqueued_at=datetime.now(UTC),
        attempt=attempt,
    )


@pytest.fixture
def dlq(mongo_database):  # type: ignore[no-untyped-def]
    return MongoDeadLetterStore(mongo_database)


def build_worker(dlq, queue, *, fail_times: int = 99, max_attempts: int = 3):  # type: ignore[no-untyped-def]
    metrics = RecordingMetrics()
    clock = SystemClock()
    return (
        Worker(
            queue=queue,
            process=ProcessEvent(
                repository=FakeRepository(fail_times=fail_times),
                search_index=None,
                metrics=metrics,
                clock=clock,
            ),
            dead_letters=dlq,
            retry_policy=RetryPolicy(
                max_attempts=max_attempts, base_seconds=0.001, cap_seconds=0.002
            ),
            clock=clock,
            metrics=metrics,
        ),
        metrics,
    )


async def test_retry_then_dead_letter(dlq) -> None:  # type: ignore[no-untyped-def]
    queue = InMemoryQueue(capacity=100, visibility_timeout=60)
    worker, metrics = build_worker(dlq, queue, max_attempts=3)

    await worker.handle(message(attempt=1))
    assert metrics.counters["processing_retries_total"] == 1
    assert await dlq.get(TENANT_A, "evt_dlq") is None  # retried, not given up on

    await worker.handle(message(attempt=3))
    letter = await dlq.get(TENANT_A, "evt_dlq")
    assert letter is not None
    assert letter.attempts == 3
    assert letter.failure_class == "processing"
    assert "ConnectionError" in letter.failure_reason
    assert letter.first_failed_at is not None


async def test_dead_letter_retains_the_complete_payload(dlq) -> None:  # type: ignore[no-untyped-def]
    queue = InMemoryQueue(capacity=100, visibility_timeout=60)
    worker, _ = build_worker(dlq, queue, max_attempts=1)
    sent = message(attempt=1)
    await worker.handle(sent)

    letter = await dlq.get(TENANT_A, "evt_dlq")
    assert letter is not None
    # Byte-for-byte what was sent, so replay needs no reconstruction (FR-018).
    assert letter.payload == sent.payload


async def test_dead_letter_replay(dlq) -> None:  # type: ignore[no-untyped-def]
    queue = InMemoryQueue(capacity=100, visibility_timeout=60)
    worker, _ = build_worker(dlq, queue, max_attempts=1)
    await worker.handle(message(attempt=1))

    replay = ReplayDeadLetter(
        store=dlq,
        queue=queue,
        clock=SystemClock(),
        publish_timeout_seconds=2.0,
        id_factory=new_id,
    )
    assert await replay(TENANT_A, "evt_dlq") == "evt_dlq"
    assert await queue.depth() == 1

    letter = await dlq.get(TENANT_A, "evt_dlq")
    assert letter is not None and letter.replayed_at is not None


async def test_replay_of_another_tenants_dead_letter_is_not_found(dlq) -> None:  # type: ignore[no-untyped-def]
    queue = InMemoryQueue(capacity=100, visibility_timeout=60)
    worker, _ = build_worker(dlq, queue, max_attempts=1)
    await worker.handle(message(attempt=1))

    replay = ReplayDeadLetter(
        store=dlq, queue=queue, clock=SystemClock(), publish_timeout_seconds=2.0, id_factory=new_id
    )
    with pytest.raises(DeadLetterNotFound):
        await replay(TENANT_B, "evt_dlq")


async def test_dead_letters_are_listed_per_tenant(dlq) -> None:  # type: ignore[no-untyped-def]
    queue = InMemoryQueue(capacity=100, visibility_timeout=60)
    worker, _ = build_worker(dlq, queue, max_attempts=1)
    for index in range(3):
        await worker.handle(message(f"evt_{index}", attempt=1))

    letters, _ = await dlq.list(TENANT_A, cursor=None, limit=50)
    assert len(letters) == 3
    empty, _ = await dlq.list(TENANT_B, cursor=None, limit=50)
    assert empty == []
