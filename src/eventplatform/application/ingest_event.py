"""Ingestion (FR-001, FR-002, FR-005).

The order matters and is the whole point of the endpoint: validate, then enqueue, then
answer. The receipt is issued only after the queue has accepted the message, so a 202
never covers an event that was dropped on the floor - and it is still not a promise of
persistence, which is why the receipt says ``accepted`` and nothing stronger.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from eventplatform.domain.errors import QueueAtCapacity
from eventplatform.domain.identity import TenantContext
from eventplatform.ports.clock import Clock
from eventplatform.ports.event_queue import EventQueue, QueueMessage
from eventplatform.ports.metrics import Metrics


@dataclass(frozen=True, slots=True)
class AcceptanceReceipt:
    event_id: str
    event_id_origin: Literal["sender", "platform"]
    received_at: str
    status: str = "accepted"


class IngestEvent:
    def __init__(
        self,
        *,
        queue: EventQueue,
        clock: Clock,
        metrics: Metrics,
        publish_timeout_seconds: float,
        id_factory: Any,
    ) -> None:
        self._queue = queue
        self._clock = clock
        self._metrics = metrics
        self._publish_timeout = publish_timeout_seconds
        self._id_factory = id_factory

    async def __call__(
        self,
        tenant: TenantContext,
        payload: dict[str, Any],
        *,
        trace_id: str | None = None,
    ) -> AcceptanceReceipt:
        supplied = payload.get("event_id")
        origin: Literal["sender", "platform"] = "sender" if supplied else "platform"
        event_id = str(supplied) if supplied else self._id_factory("evt")
        received_at = self._clock.now()

        message = QueueMessage(
            message_id=self._id_factory("msg"),
            tenant_id=tenant.tenant_id,
            event_id=event_id,
            payload={**payload, "event_id": event_id, "received_at": received_at.isoformat()},
            enqueued_at=received_at,
            trace_id=trace_id,
        )

        try:
            await self._queue.publish(message, timeout_seconds=self._publish_timeout)
        except QueueAtCapacity:
            # Counted before re-raising so backpressure is visible in metrics even when
            # every one of these requests is being rejected.
            self._metrics.increment("ingest_rejected_backpressure_total")
            raise

        self._metrics.increment(
            "events_accepted_total",
            tenant_id=tenant.tenant_id,
            event_type=str(payload.get("event_type", "unknown")),
        )
        return AcceptanceReceipt(
            event_id=event_id,
            event_id_origin=origin,
            received_at=received_at.isoformat(),
        )
