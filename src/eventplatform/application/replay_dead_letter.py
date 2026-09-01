"""Dead-letter replay (FR-018).

Republishes the stored payload unchanged. Idempotency still applies on reprocessing, so
replaying an event that did eventually get stored is harmless - which is what makes
replay safe to run without first proving the event is absent.
"""

from __future__ import annotations

from typing import Any

from eventplatform.domain.errors import DeadLetterNotFound
from eventplatform.domain.identity import TenantContext
from eventplatform.ports.clock import Clock
from eventplatform.ports.dead_letter_store import DeadLetterStore
from eventplatform.ports.event_queue import EventQueue, QueueMessage


class ReplayDeadLetter:
    def __init__(
        self,
        *,
        store: DeadLetterStore,
        queue: EventQueue,
        clock: Clock,
        publish_timeout_seconds: float,
        id_factory: Any,
    ) -> None:
        self._store = store
        self._queue = queue
        self._clock = clock
        self._publish_timeout = publish_timeout_seconds
        self._id_factory = id_factory

    async def __call__(self, tenant: TenantContext, event_id: str) -> str:
        letter = await self._store.get(tenant, event_id)
        if letter is None:
            raise DeadLetterNotFound(f"no dead letter {event_id} for this tenant")

        now = self._clock.now()
        await self._queue.publish(
            QueueMessage(
                message_id=self._id_factory("msg"),
                tenant_id=tenant.tenant_id,
                event_id=event_id,
                payload=letter.payload,
                enqueued_at=now,
                metadata={"replay": "true"},
            ),
            timeout_seconds=self._publish_timeout,
        )
        await self._store.mark_replayed(tenant, event_id, at=now)
        return event_id
