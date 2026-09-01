# Queue Message Contract

**Plan**: [plan.md](../plan.md) | **Decisions**: [research.md](../research.md) R2, R3, R12

One port, `EventQueue`, with two implementations. The message shape and the delivery semantics below
are identical across both, because the domain must not be able to tell which is mounted.

## Message envelope

```json
{
  "message_id": "msg_01J...",
  "tenant_id": "tenant_01H...",
  "event_id": "evt_01J...",
  "payload": { "...validated event submission..." },
  "attempt": 1,
  "enqueued_at": "2026-08-31T20:30:01Z",
  "trace_id": "req_01J..."
}
```

`message_id` identifies the *delivery*; `event_id` identifies the *event*. They are deliberately
distinct: a single event may arrive under several message ids, which is precisely the case
idempotency exists to absorb. `trace_id` carries the originating request id so a dead letter can be
traced back to the HTTP call that produced it.

## Delivery semantics

| Property | Guarantee | Notes |
|---|---|---|
| Delivery | At-least-once | Never assume exactly-once. Consumers must be idempotent (FR-011). |
| Ordering | None | No global or per-tenant ordering. Events carry `occurred_at`; order is a read-time concern. |
| Visibility timeout | Configurable, default 30 s | A message being processed is invisible to other consumers; on timeout it is redelivered. |
| Redelivery | Up to 5 attempts | Then dead-lettered with failure metadata (FR-017). |
| Backoff | Exponential with full jitter | `min(1s * 2^(n-1), 60s)`, computed by domain policy, identical in both adapters. |
| Acknowledgement | After canonical persistence only | FR-015. Never before. |
| Capacity | Bounded | At capacity the publish fails fast and the API returns 503 (FR-005). |

## What the in-process adapter does NOT provide

Stated here rather than buried, because it is the honest answer to "what would change with real
SQS":

| Property | In-process | RabbitMQ (quorum) | AWS SQS |
|---|---|---|---|
| Survives process restart | **No — queued messages are lost** | Yes | Yes |
| Survives broker/node loss | n/a | Yes, Raft-replicated | Yes, managed |
| Visibility timeout | Simulated in-process | Simulated via unacked delivery | Native |
| Redelivery after consumer crash | Only if the process lives | Yes | Yes |
| Dead-letter routing | Application-level store | Dead-letter exchange | Native DLQ redrive |
| Backpressure signal | Bounded queue full | Flow control blocks publishers | Effectively unbounded; throttle at the API |
| Ordering | FIFO within the process | Per-queue, not guaranteed under redelivery | FIFO queues only, with throughput limits |
| Max message size | Memory | 128 MB default | 256 KB — would force a claim-check for large payloads |

The SQS column is the ADR-007 migration target. Two rows drive real work: the 256 KB message limit
means an oversized payload needs a claim-check indirection through object storage, and SQS's
long-poll receive loop replaces the adapter's push-style consumer. Neither reaches the domain.

## Port interface

```python
class EventQueue(Protocol):
    async def publish(self, message: QueueMessage, *, timeout_seconds: float) -> None: ...
    async def receive(self, *, max_messages: int, wait: float) -> list[QueueMessage]: ...
    async def acknowledge(self, message_id: str) -> None: ...
    async def retry_later(self, message: QueueMessage, *, delay: float) -> None: ...
    async def depth(self) -> int: ...
```

`publish` takes an explicit bound because a broker under flow control blocks publishers
indefinitely; without it a broker slowdown becomes an API outage (R12).
