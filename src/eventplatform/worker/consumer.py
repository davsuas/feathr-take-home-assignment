"""The worker loop (FR-015..FR-019).

The ordering rule this file exists to enforce: a message is acknowledged only after the
canonical record is confirmed stored. Everything else - projection, metrics, logging -
happens after that point or not at all.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from eventplatform.application.mapping import build_event
from eventplatform.application.process_event import ProcessEvent
from eventplatform.domain.errors import ValidationError
from eventplatform.domain.identity import TenantContext
from eventplatform.domain.policies.retry import RetryPolicy
from eventplatform.domain.policies.time_window import TimeWindowPolicy
from eventplatform.infrastructure.observability.correlation import correlation_scope, new_id
from eventplatform.infrastructure.observability.logging import get_logger
from eventplatform.ports.clock import Clock
from eventplatform.ports.dead_letter_store import DeadLetter, DeadLetterStore
from eventplatform.ports.event_queue import EventQueue, QueueMessage
from eventplatform.ports.metrics import Metrics

log = get_logger(__name__)


class Worker:
    def __init__(
        self,
        *,
        queue: EventQueue,
        process: ProcessEvent,
        dead_letters: DeadLetterStore,
        retry_policy: RetryPolicy,
        clock: Clock,
        metrics: Metrics,
        time_policy: TimeWindowPolicy | None = None,
        worker_id: str | None = None,
    ) -> None:
        self._queue = queue
        self._process = process
        self._dead_letters = dead_letters
        self._retry = retry_policy
        self._clock = clock
        self._metrics = metrics
        self._time_policy = time_policy or TimeWindowPolicy()
        self._worker_id = worker_id or new_id("wrk")
        self._running = False

    async def run_forever(self, *, batch_size: int = 10, wait: float = 1.0) -> None:
        self._running = True
        while self._running:
            messages = await self._queue.receive(max_messages=batch_size, wait=wait)
            for message in messages:
                await self.handle(message)
            self._metrics.gauge("queue_depth", await self._queue.depth())

    def stop(self) -> None:
        self._running = False

    async def drain(self, *, batch_size: int = 50, wait: float = 0.05) -> int:
        """Process whatever is currently queued and return. Used by tests and by the
        replay path, where an unbounded loop would never hand control back."""
        handled = 0
        while True:
            messages = await self._queue.receive(max_messages=batch_size, wait=wait)
            if not messages:
                return handled
            for message in messages:
                await self.handle(message)
                handled += 1

    async def handle(self, message: QueueMessage) -> None:
        tenant = TenantContext(tenant_id=message.tenant_id, credential_hash="worker")
        with correlation_scope(
            tenant_id=message.tenant_id,
            event_id=message.event_id,
            worker_id=self._worker_id,
            request_id=message.trace_id,
        ):
            received_at = self._parse_received_at(message)
            try:
                event = build_event(
                    tenant, message.payload, received_at=received_at, time_policy=self._time_policy
                )
            except ValidationError as exc:
                # A poison message cannot be fixed by retrying it. Dead-letter at once so
                # it stops consuming attempts and stops blocking the batch behind it.
                await self._dead_letter(message, exc, failure_class="validation")
                await self._queue.acknowledge(message)
                return

            try:
                result = await self._process(tenant, event)
            except Exception as exc:
                await self._handle_failure(message, exc)
                return

            await self._queue.acknowledge(message)
            log.info(
                "event_processed",
                outcome=result.outcome.value,
                projected=result.projected,
                attempt=message.attempt,
            )

    async def _handle_failure(self, message: QueueMessage, exc: Exception) -> None:
        self._metrics.increment("mongo_write_failures_total")
        if self._retry.should_retry(message.attempt):
            delay = self._retry.delay_for(message.attempt)
            self._metrics.increment("processing_retries_total")
            log.warning(
                "processing_failed_retrying",
                attempt=message.attempt,
                delay_seconds=round(delay, 3),
                error=type(exc).__name__,
            )
            await self._queue.retry_later(message, delay=delay)
            return

        await self._dead_letter(message, exc, failure_class="processing")
        await self._queue.acknowledge(message)

    async def _dead_letter(
        self, message: QueueMessage, exc: Exception, *, failure_class: str
    ) -> None:
        now = self._clock.now()
        tenant = TenantContext(tenant_id=message.tenant_id, credential_hash="worker")
        await self._dead_letters.record(
            tenant,
            DeadLetter(
                tenant_id=message.tenant_id,
                event_id=message.event_id,
                payload=message.payload,
                failure_reason=f"{type(exc).__name__}: {exc}",
                failure_class=failure_class,
                attempts=message.attempt,
                first_failed_at=now,
                last_failed_at=now,
            ),
        )
        self._metrics.increment("dead_letter_total")
        log.error(
            "event_dead_lettered",
            attempts=message.attempt,
            failure_class=failure_class,
            error=type(exc).__name__,
        )

    def _parse_received_at(self, message: QueueMessage) -> datetime:
        raw: Any = message.payload.get("received_at")
        if isinstance(raw, datetime):
            return raw
        if isinstance(raw, str):
            try:
                return datetime.fromisoformat(raw)
            except ValueError:
                pass
        return self._clock.now()


async def run_worker(worker: Worker) -> None:
    try:
        await worker.run_forever()
    except asyncio.CancelledError:
        worker.stop()
        raise
