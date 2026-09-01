# ADR-007: Migration path from the in-process queue to AWS SQS

## Status

Accepted — 2026-08-31

## Context

The assignment specifies a simulated in-process queue and asks what would change with real
SQS. Answering that in prose is cheap and unfalsifiable — anyone can write "we would swap
the adapter".

The in-process queue has one limitation that matters more than all the others, and it
should be stated before anything else: **messages accepted but not yet processed are lost
when the process restarts.** A `202 Accepted` that survives no further than process memory
is not durable acceptance. Every other difference is secondary to that one.

## Decision

**One `EventQueue` port, two live implementations, SQS as a visible third.**

- `InMemoryQueue` — `asyncio.Queue` with bounded capacity, visibility timeout, redelivery,
  and attempt counting. The default, and what the test suite drives, so the suite needs no
  broker and runs on one command.
- `RabbitMQQueue` — aio-pika, quorum queues, explicit prefetch, TTL retry ladder. What
  Docker Compose runs.

Having two implementations already behind the port turns "SQS is a third adapter" from a
claim into an observation.

### What actually differs

| Property | In-process | RabbitMQ (quorum) | AWS SQS |
|---|---|---|---|
| Survives process restart | **No — queued messages lost** | Yes | Yes |
| Survives node loss | n/a | Yes, Raft-replicated | Yes, managed |
| Visibility timeout | Simulated in-process | Unacked delivery | Native |
| Redelivery after consumer crash | Only while the process lives | Yes | Yes |
| Dead-letter routing | Application-level store | Dead-letter exchange | Native DLQ + redrive |
| Backpressure signal | Bounded queue full | Flow control blocks publishers | Effectively unbounded; throttle at the API |
| Ordering | FIFO within the process | Per-queue, not under redelivery | FIFO queues only, with throughput limits |
| Max message size | Memory | 128 MB | **256 KB** |
| Receive model | Direct call | Push (consume) | **Long-poll** |

### The two rows that carry real work

**256 KB message limit.** Our metadata cap is 16 KB, so a typical event fits comfortably.
An event near the payload ceiling plus envelope could not. The fix is a claim-check: write
the payload to S3, put the key on the queue, fetch it in the worker. This lands entirely
inside the adapter — the domain still receives a `QueueMessage` with a `payload` dict.

**Long-poll receive.** SQS has no push delivery; the consumer calls `ReceiveMessage` with
`WaitTimeSeconds`. The port's `receive(max_messages, wait)` signature was shaped for this
from the start, so the SQS adapter is the *most* natural fit of the three.

### What does not change

Retry schedule (a pure domain function), idempotency (a MongoDB unique index), the worker
loop, every use case, and every test that does not name a broker. Backoff would move from
the TTL ladder to SQS's per-message `VisibilityTimeout`, computed from the same domain
function.

## Consequences

**Easier.** The migration is one new file in `infrastructure/queue/`, a config value, and
IAM. No domain or application change.

**Harder.** SQS's at-least-once delivery is stronger than RabbitMQ's in practice, so
duplicates become more common, not less — standard queues genuinely redeliver. ADR-002 is
what makes that a non-event.

**Honest about the demo.** The in-process adapter's restart behaviour is row one of
`docs/failure-modes.md`. It is not excused; it is the reason the other two adapters exist.

**Rejected: SQS only, no in-process adapter.** Every test would need AWS credentials or
LocalStack, violating constitution X.3, and the assignment's stated in-process queue would
be gone.

**Rejected: Kafka.** Partition and consumer-group semantics are a much larger topic than
this at-least-once framing needs, and its ordering guarantees would be paid for without
being used.
