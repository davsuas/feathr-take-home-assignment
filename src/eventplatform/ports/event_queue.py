from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class QueueMessage:
    """``message_id`` identifies the delivery; ``event_id`` identifies the event.

    They are deliberately distinct: one event may arrive under several message ids, which
    is precisely the case idempotency exists to absorb.
    """

    message_id: str
    tenant_id: str
    event_id: str
    payload: dict[str, Any]
    enqueued_at: datetime
    attempt: int = 1
    trace_id: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


class EventQueue(Protocol):
    async def publish(self, message: QueueMessage, *, timeout_seconds: float) -> None:
        """Raise ``QueueAtCapacity`` rather than block indefinitely.

        The bound is not optional and lives in the contract rather than at the call site:
        a broker under flow control blocks publishers, and without it a broker slowdown
        turns POST /events into a hanging request. Adapters enforce it with
        ``asyncio.timeout``.
        """

    async def receive(self, *, max_messages: int, wait: float) -> list[QueueMessage]: ...
    async def acknowledge(self, message: QueueMessage) -> None: ...
    async def retry_later(self, message: QueueMessage, *, delay: float) -> None: ...
    async def depth(self) -> int: ...
    async def close(self) -> None: ...
