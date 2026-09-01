# ADR-002: At-least-once delivery with consumer-side idempotency

## Status

Accepted — 2026-08-31

## Context

Queues redeliver. A worker crashes between persisting and acknowledging; a visibility
timeout expires while processing is merely slow; a sender times out and retries. Every one
of these produces a second delivery of an event that may already be stored.

Exactly-once delivery is not available. What is available is at-least-once delivery plus
an idempotent consumer, which produces exactly-once *effects* — the property that actually
matters. For this domain the stakes are concrete: a double-counted donation is a wrong
number on a fundraising report, and the organisation makes decisions on it.

Duplicates arrive from two directions, and a design that handles only one is incomplete:

1. Internal redelivery — the broker delivers the same message twice.
2. Sender retry — the client never got a response and submits again.

## Decision

**At-least-once, absorbed by a unique index.**

- Idempotency key: `(tenant_id, event_id)`, enforced by a unique MongoDB index.
- The worker issues `insert_one` and treats `DuplicateKeyError` as a normal outcome.
- A `content_hash` (SHA-256 of the canonical envelope) distinguishes a benign duplicate
  from a conflicting one. Matching hash means the same event arrived twice; differing hash
  means two events claimed one id, and the first stored version wins.
- `event_id` is sender-supplied when present, platform-generated when absent, so a sender's
  own retry is deduplicated (spec decision D2).
- The queue message is acknowledged only after canonical persistence is confirmed.

Insert returns an outcome rather than raising:
`INSERTED | DUPLICATE_SUPPRESSED | DUPLICATE_CONFLICT`. All three acknowledge — the event
is durably resolved in every case — and each increments a distinct counter.

## Consequences

**Easier.** Adding worker replicas is safe by construction. Replaying a dead letter is safe
even if the event did eventually store. Redelivery needs no coordination.

**Harder.** `content_hash` must exclude anything that legitimately differs between two
deliveries of the same event. It excludes `received_at` and `projection` for exactly this
reason — including `received_at` would make every redelivery look like a conflict. This is
subtle enough that a test asserts it directly.

**A cost accepted in the open.** A sender that omits `event_id` and then retries produces
two events. The acceptance response carries `event_id_origin`, so the sender is told which
path was taken, and `docs/event-contract.md` tells them to supply their own id.

**Rejected: read-then-write.** Check whether the event exists, then insert. It loses the
race between two workers processing the same redelivery — both read "absent", both insert.
A unique index cannot lose that race, which is why the constraint lives in the storage
engine rather than in application logic. `test_concurrent_duplicates_cannot_both_insert`
runs eight concurrent inserts and asserts exactly one wins.

**Rejected: upsert with `$setOnInsert`.** Idempotent, but silently succeeds on a
conflicting duplicate, discarding the FR-014 signal that two different events collided.

**Rejected: a Redis dedup set.** Fast, but makes an evictable derived store load-bearing
for correctness, contradicting ADR-003.
