"""RabbitMQ adapter (research.md R2, R3).

Three details here are the difference between working and subtly broken:

1. ``aio_pika``, never ``pika``. pika is synchronous; calling it from a coroutine blocks
   the event loop and serialises the entire API.
2. RabbitMQ has no delayed redelivery. ``nack(requeue=True)`` redelivers immediately,
   which turns a failing message into a hot loop. The workaround is a ladder of retry
   queues that have a per-message TTL and no consumer, dead-lettering back to the work
   exchange when the TTL expires.
3. ``prefetch_count`` must be set. The default is unlimited, which lets one consumer pull
   the whole queue into memory, starve its peers, and eventually get OOM-killed.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractQueue, AbstractRobustConnection

from eventplatform.domain.errors import QueueAtCapacity
from eventplatform.ports.event_queue import QueueMessage

WORK_EXCHANGE = "events.work"
WORK_QUEUE = "q.events.persist"
RETRY_EXCHANGE = "events.retry"
DLQ_QUEUE = "q.events.dead"
ROUTING_KEY = "event.persist"

# Escalating TTLs. A message dead-letters from a retry queue back to the work exchange
# once its TTL expires, which is how backoff is expressed without a plugin.
RETRY_LADDER_MS = (5_000, 30_000, 120_000)


class RabbitMQQueue:
    def __init__(self, url: str, *, prefetch_count: int = 50) -> None:
        self._url = url
        self._prefetch = prefetch_count
        self._connection: AbstractRobustConnection | None = None
        self._channel: AbstractChannel | None = None
        self._queue: AbstractQueue | None = None
        self._unacked: dict[str, aio_pika.abc.AbstractIncomingMessage] = {}

    async def connect(self) -> None:
        self._connection = await aio_pika.connect_robust(self._url)
        self._channel = await self._connection.channel()
        await self._channel.set_qos(prefetch_count=self._prefetch)

        work = await self._channel.declare_exchange(
            WORK_EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
        )
        retry = await self._channel.declare_exchange(
            RETRY_EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
        )

        # Quorum queues (Raft-replicated), not classic mirrored queues, which are
        # deprecated.
        self._queue = await self._channel.declare_queue(
            WORK_QUEUE, durable=True, arguments={"x-queue-type": "quorum"}
        )
        await self._queue.bind(work, ROUTING_KEY)

        for index, ttl in enumerate(RETRY_LADDER_MS):
            retry_queue = await self._channel.declare_queue(
                f"q.events.retry.{index}",
                durable=True,
                arguments={
                    "x-queue-type": "quorum",
                    "x-message-ttl": ttl,
                    "x-dead-letter-exchange": WORK_EXCHANGE,
                    "x-dead-letter-routing-key": ROUTING_KEY,
                },
            )
            await retry_queue.bind(retry, f"retry.{index}")

        dead = await self._channel.declare_queue(
            DLQ_QUEUE, durable=True, arguments={"x-queue-type": "quorum"}
        )
        await dead.bind(work, "event.dead")

    async def publish(self, message: QueueMessage, *, timeout_seconds: float) -> None:
        if self._channel is None:
            await self.connect()
        assert self._channel is not None
        exchange = await self._channel.get_exchange(WORK_EXCHANGE)
        try:
            # The timeout is what stops a broker memory alarm - which blocks publishers -
            # from turning POST /events into an indefinitely hanging request.
            async with asyncio.timeout(timeout_seconds):
                await exchange.publish(
                    aio_pika.Message(
                        body=json.dumps(_serialize(message)).encode(),
                        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                        message_id=message.message_id,
                        headers={"attempt": message.attempt},
                    ),
                    routing_key=ROUTING_KEY,
                )
        except TimeoutError as exc:
            raise QueueAtCapacity("broker did not accept the publish in time") from exc

    async def receive(self, *, max_messages: int, wait: float) -> list[QueueMessage]:
        if self._queue is None:
            await self.connect()
        assert self._queue is not None
        out: list[QueueMessage] = []
        deadline = asyncio.get_running_loop().time() + wait
        while len(out) < max_messages:
            incoming = await self._queue.get(fail=False, timeout=max(0.01, wait))
            if incoming is None:
                if asyncio.get_running_loop().time() >= deadline:
                    break
                await asyncio.sleep(0.01)
                continue
            parsed = _deserialize(json.loads(incoming.body))
            self._unacked[parsed.message_id] = incoming
            out.append(parsed)
        return out

    async def acknowledge(self, message: QueueMessage) -> None:
        incoming = self._unacked.pop(message.message_id, None)
        if incoming is not None:
            await incoming.ack()

    async def retry_later(self, message: QueueMessage, *, delay: float) -> None:
        """Route to the retry rung whose TTL is closest to the requested delay.

        The domain computes an exact backoff; the broker offers fixed rungs. Rounding to
        the nearest rung keeps one schedule in the domain rather than two definitions of
        backoff that drift apart.
        """
        incoming = self._unacked.pop(message.message_id, None)
        if incoming is not None:
            await incoming.ack()
        if self._channel is None:
            await self.connect()
        assert self._channel is not None

        target_ms = delay * 1000
        rung = min(range(len(RETRY_LADDER_MS)), key=lambda i: abs(RETRY_LADDER_MS[i] - target_ms))
        retry_exchange = await self._channel.get_exchange(RETRY_EXCHANGE)
        await retry_exchange.publish(
            aio_pika.Message(
                body=json.dumps(_serialize(replace(message, attempt=message.attempt + 1))).encode(),
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                message_id=message.message_id,
            ),
            routing_key=f"retry.{rung}",
        )

    async def depth(self) -> int:
        if self._queue is None:
            return 0
        declared = await self._queue.channel.declare_queue(WORK_QUEUE, durable=True, passive=True)
        return int(declared.declaration_result.message_count or 0)

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None
            self._channel = None
            self._queue = None


def _serialize(message: QueueMessage) -> dict[str, Any]:
    return {
        "message_id": message.message_id,
        "tenant_id": message.tenant_id,
        "event_id": message.event_id,
        "payload": message.payload,
        "enqueued_at": message.enqueued_at.isoformat(),
        "attempt": message.attempt,
        "trace_id": message.trace_id,
        "metadata": message.metadata,
    }


def _deserialize(raw: dict[str, Any]) -> QueueMessage:
    return QueueMessage(
        message_id=raw["message_id"],
        tenant_id=raw["tenant_id"],
        event_id=raw["event_id"],
        payload=raw["payload"],
        enqueued_at=datetime.fromisoformat(raw["enqueued_at"]).astimezone(UTC),
        attempt=raw.get("attempt", 1),
        trace_id=raw.get("trace_id"),
        metadata=raw.get("metadata", {}),
    )
