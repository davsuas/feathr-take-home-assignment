"""In-process queue adapter (research.md R2).

Models SQS-style semantics inside one process: bounded capacity, visibility timeout,
redelivery, and attempt counting.

What it cannot do, stated plainly rather than left implied: messages accepted but not yet
processed are lost when the process restarts. That is the honest limitation this whole
design is arranged around, and it is why the RabbitMQ adapter and ADR-007 exist.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import replace
from datetime import UTC, datetime

from eventplatform.domain.errors import QueueAtCapacity
from eventplatform.ports.event_queue import QueueMessage


class InMemoryQueue:
    def __init__(self, *, capacity: int = 10_000, visibility_timeout: float = 30.0) -> None:
        self._queue: asyncio.Queue[QueueMessage] = asyncio.Queue(maxsize=capacity)
        self._visibility_timeout = visibility_timeout
        self._in_flight: dict[str, asyncio.Task[None]] = {}
        self._pending_retries: set[asyncio.Task[None]] = set()
        self._capacity = capacity

    async def publish(self, message: QueueMessage, *, timeout_seconds: float) -> None:
        try:
            # A bounded wait, not put_nowait: a brief burst should ride out a momentarily
            # full queue, while a sustained one must reject rather than block the caller.
            async with asyncio.timeout(timeout_seconds):
                await self._queue.put(message)
        except (asyncio.QueueFull, TimeoutError) as exc:
            raise QueueAtCapacity(
                f"queue is at capacity ({self._capacity}); event was not accepted"
            ) from exc

    async def receive(self, *, max_messages: int, wait: float) -> list[QueueMessage]:
        messages: list[QueueMessage] = []
        try:
            async with asyncio.timeout(wait):
                messages.append(await self._queue.get())
        except TimeoutError:
            return []
        while len(messages) < max_messages:
            try:
                messages.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        for message in messages:
            self._start_visibility_timer(message)
        return messages

    def _start_visibility_timer(self, message: QueueMessage) -> None:
        async def redeliver() -> None:
            await asyncio.sleep(self._visibility_timeout)
            # Timer survived, so the consumer never acknowledged: put it back with the
            # attempt count advanced, exactly as a broker would on visibility expiry.
            self._in_flight.pop(message.message_id, None)
            with contextlib.suppress(asyncio.QueueFull):
                self._queue.put_nowait(replace(message, attempt=message.attempt + 1))

        self._in_flight[message.message_id] = asyncio.create_task(redeliver())

    async def acknowledge(self, message: QueueMessage) -> None:
        task = self._in_flight.pop(message.message_id, None)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def retry_later(self, message: QueueMessage, *, delay: float) -> None:
        await self.acknowledge(message)
        retried = replace(message, attempt=message.attempt + 1, enqueued_at=datetime.now(UTC))

        async def requeue() -> None:
            await asyncio.sleep(delay)
            with contextlib.suppress(asyncio.QueueFull):
                self._queue.put_nowait(retried)

        # Fire-and-forget on purpose: holding the worker for the backoff would keep the
        # in-flight slot and let one slow dependency stall healthy messages.
        task = asyncio.create_task(requeue())
        self._pending_retries.add(task)
        task.add_done_callback(self._pending_retries.discard)

    async def depth(self) -> int:
        return self._queue.qsize()

    async def close(self) -> None:
        for task in [*self._in_flight.values(), *self._pending_retries]:
            task.cancel()
        self._in_flight.clear()
        self._pending_retries.clear()
