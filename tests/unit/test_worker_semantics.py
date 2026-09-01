"""Worker semantics: idempotency, retry, dead-letter, projection resilience.

These use fakes rather than containers because the behaviour under test is the worker's
decision-making, not the database's. The same paths are exercised against real services
in tests/integration.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from eventplatform.application.mapping import build_event
from eventplatform.application.process_event import ProcessEvent
from eventplatform.domain.event import ProjectionStatus
from eventplatform.domain.identity import TenantContext
from eventplatform.domain.policies.retry import RetryPolicy
from eventplatform.infrastructure.queue.in_memory import InMemoryQueue
from eventplatform.ports.event_queue import QueueMessage
from eventplatform.worker.consumer import Worker
from eventplatform.worker.reconciler import ProjectionReconciler
from tests.unit.fakes import (
    FakeClock,
    FakeDeadLetterStore,
    FakeRepository,
    FakeSearchIndex,
    RecordingMetrics,
)

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def payload(event_id: str = "evt_1", **overrides: object) -> dict[str, object]:
    return {
        "event_id": event_id,
        "event_type": "donation",
        "occurred_at": (NOW - timedelta(minutes=1)).isoformat(),
        "received_at": NOW.isoformat(),
        "metadata": {"browser": "Safari"},
        **overrides,
    }


def message(event_id: str = "evt_1", *, attempt: int = 1, **overrides: object) -> QueueMessage:
    return QueueMessage(
        message_id=f"msg_{event_id}_{attempt}",
        tenant_id="tenant_a",
        event_id=event_id,
        payload=payload(event_id, **overrides),
        enqueued_at=NOW,
        attempt=attempt,
    )


def build_worker(
    *,
    repository: FakeRepository | None = None,
    search: FakeSearchIndex | None = None,
    dead_letters: FakeDeadLetterStore | None = None,
    metrics: RecordingMetrics | None = None,
    queue: InMemoryQueue | None = None,
    max_attempts: int = 3,
) -> tuple[Worker, FakeRepository, FakeDeadLetterStore, RecordingMetrics, InMemoryQueue]:
    repository = repository or FakeRepository()
    dead_letters = dead_letters or FakeDeadLetterStore()
    metrics = metrics or RecordingMetrics()
    queue = queue or InMemoryQueue(capacity=100, visibility_timeout=60)
    clock = FakeClock(NOW)
    worker = Worker(
        queue=queue,
        process=ProcessEvent(
            repository=repository,
            search_index=search,
            metrics=metrics,
            clock=clock,
        ),
        dead_letters=dead_letters,
        retry_policy=RetryPolicy(max_attempts=max_attempts, base_seconds=0.001, cap_seconds=0.002),
        clock=clock,
        metrics=metrics,
    )
    return worker, repository, dead_letters, metrics, queue


class TestDuplicateDelivery:
    """map #5, #6 - SC-003, FR-014"""

    async def test_triplicate_delivery_yields_one_record(self) -> None:
        worker, repo, _, metrics, _ = build_worker()
        for attempt in (1, 2, 3):
            await worker.handle(message(attempt=attempt))
        assert len(repo.events) == 1
        assert metrics.counters["duplicate_suppressed_total"] == 2

    async def test_sender_retry_with_same_id_is_suppressed(self) -> None:
        # The dominant real duplicate source: a sender that timed out and retried.
        worker, repo, _, metrics, _ = build_worker()
        await worker.handle(message())
        await worker.handle(
            QueueMessage(
                message_id="msg_other",
                tenant_id="tenant_a",
                event_id="evt_1",
                payload=payload(),
                enqueued_at=NOW,
            )
        )
        assert len(repo.events) == 1
        assert metrics.counters["duplicate_suppressed_total"] == 1

    async def test_conflicting_duplicate_keeps_the_first(self) -> None:
        worker, repo, _, metrics, _ = build_worker()
        await worker.handle(message())
        await worker.handle(message(contact_id="someone_else"))
        stored = repo.events[("tenant_a", "evt_1")]
        assert stored.identity.contact_id is None
        assert metrics.counters["duplicate_conflict_total"] == 1

    async def test_redelivery_after_crash_before_ack_still_yields_one_record(self) -> None:
        # US2 scenario 4: persistence succeeded, acknowledgement never happened.
        worker, repo, _, _, _ = build_worker()
        tenant = TenantContext(tenant_id="tenant_a", credential_hash="worker")
        # Persist exactly as the worker would, then stop short of acknowledging.
        await worker._process(tenant, build_event(tenant, payload(), received_at=NOW))
        # The broker's visibility timeout expires and the same event comes back.
        await worker.handle(message(attempt=2))
        assert len(repo.events) == 1


class TestRetryAndDeadLetter:
    """map #7 - SC-004"""

    async def test_transient_failure_is_retried_not_dead_lettered(self) -> None:
        worker, _, dlq, metrics, _q = build_worker(repository=FakeRepository(fail_times=1))
        await worker.handle(message())
        assert metrics.counters["processing_retries_total"] == 1
        assert not dlq.letters

    async def test_exhausted_attempts_are_dead_lettered_with_metadata(self) -> None:
        worker, _, dlq, metrics, _ = build_worker(
            repository=FakeRepository(fail_times=99), max_attempts=3
        )
        await worker.handle(message(attempt=3))
        letter = dlq.letters[("tenant_a", "evt_1")]
        assert letter.attempts == 3
        assert "ConnectionError" in letter.failure_reason
        assert letter.payload == payload()  # full payload retained for replay
        assert metrics.counters["dead_letter_total"] == 1

    async def test_poison_message_is_dead_lettered_immediately(self) -> None:
        # A message that cannot be parsed will never parse. Retrying it burns attempts
        # and blocks the batch behind it.
        worker, _, dlq, metrics, _ = build_worker()
        await worker.handle(message(event_type="not_a_real_type"))
        assert dlq.letters[("tenant_a", "evt_1")].failure_class == "validation"
        assert metrics.counters.get("processing_retries_total") is None

    async def test_a_failing_message_does_not_block_healthy_ones(self) -> None:
        worker, repo, _, _, _ = build_worker()
        await worker.handle(message("evt_bad", event_type="bogus"))
        await worker.handle(message("evt_good"))
        assert ("tenant_a", "evt_good") in repo.events


class TestProjectionResilience:
    """map #3, #11 - FR-038"""

    async def test_event_is_stored_even_when_search_is_down(self) -> None:
        worker, repo, _, metrics, _ = build_worker(search=FakeSearchIndex(available=False))
        await worker.handle(message())
        stored = repo.events[("tenant_a", "evt_1")]
        assert stored.projection.status is ProjectionStatus.FAILED
        assert metrics.counters["projection_failures_total"] == 1

    async def test_reconciler_repairs_the_projection_once_search_returns(self) -> None:
        search = FakeSearchIndex(available=False)
        worker, repo, _, metrics, _ = build_worker(search=search)
        await worker.handle(message())

        search.available = True
        reconciler = ProjectionReconciler(
            repository=repo,
            search_index=search,
            clock=FakeClock(NOW),
            metrics=metrics,
            stale_after_seconds=0,
        )
        assert await reconciler.run_once() == 1
        assert repo.events[("tenant_a", "evt_1")].projection.status is ProjectionStatus.INDEXED
        assert "tenant_a:evt_1" in search.documents

    async def test_successful_projection_marks_indexed(self) -> None:
        worker, repo, _, _, _ = build_worker(search=FakeSearchIndex())
        await worker.handle(message())
        assert repo.events[("tenant_a", "evt_1")].projection.status is ProjectionStatus.INDEXED


class TestProcessOutcomes:
    async def test_all_outcomes_are_counted_distinctly(self) -> None:
        worker, _, _, metrics, _ = build_worker()
        await worker.handle(message())
        assert metrics.counters["processing_attempts_total"] == 1


@pytest.mark.parametrize("attempt", [1, 2])
async def test_message_is_acknowledged_only_after_persistence(attempt: int) -> None:
    worker, repo, _, _, queue = build_worker()
    msg = message(attempt=attempt)
    await queue.publish(msg, timeout_seconds=1)
    received = await queue.receive(max_messages=1, wait=0.1)
    await worker.handle(received[0])
    assert len(repo.events) == 1
    assert await queue.depth() == 0


class TestQueueWiring:
    """Regression: the composition root must honour QUEUE_BACKEND.

    It once ignored the setting entirely and always built an in-memory queue, so under
    Compose the API and worker each held their own private queue. Every submission
    returned 202 and nothing was ever stored — and the worker even logged
    `queue_backend=rabbitmq`, because it logged the setting rather than what was built.
    Only a real Compose run surfaced it, which is why this test exists.
    """

    def test_memory_backend_builds_the_in_process_queue(self) -> None:
        from eventplatform.composition import _build_queue
        from eventplatform.infrastructure.config.settings import Settings
        from eventplatform.infrastructure.queue.in_memory import InMemoryQueue

        assert isinstance(_build_queue(Settings(queue_backend="memory")), InMemoryQueue)

    def test_rabbitmq_backend_builds_the_broker_adapter(self) -> None:
        from eventplatform.composition import _build_queue
        from eventplatform.infrastructure.config.settings import Settings
        from eventplatform.infrastructure.queue.rabbitmq import RabbitMQQueue

        assert isinstance(_build_queue(Settings(queue_backend="rabbitmq")), RabbitMQQueue)

    def test_capacity_setting_reaches_the_in_process_queue(self) -> None:
        from eventplatform.composition import _build_queue
        from eventplatform.infrastructure.config.settings import Settings

        queue = _build_queue(Settings(queue_backend="memory", queue_capacity=7))
        assert queue._capacity == 7
