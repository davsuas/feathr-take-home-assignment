# Phase 0 Research: Multi-Tenant Distributed Event Processing Platform

**Date**: 2026-08-31 | **Plan**: [plan.md](./plan.md)

**Not to be confused with** `docs/research.md`, which is prior research on *how to document*
architecture (arc42, C4, ADRs) and is an input to this plan. This file records the *technical*
decisions for this feature, each as decision, rationale, and alternatives rejected.

No `NEEDS CLARIFICATION` markers remained in the spec entering Phase 0; the two open questions were
resolved as D1 and D2 before planning began. The items below are design decisions, not open
questions.

---

## R1: Async MongoDB driver — PyMongo native async, not Motor

**Decision**: Use `pymongo.AsyncMongoClient` from PyMongo 4.17.0.

**Rationale**: Motor's own PyPI page carries the notice that it is deprecated as of 2026-05-14, one
year after PyMongo's native async API reached production readiness. That date has passed. Adopting
a driver that is already past its deprecation date would be a defect in a submission whose
constitution requires "current supported versions verified at implementation time" (XI.2). PyMongo's
async API is the successor, ships in the same package as the sync driver, and needs no separate
dependency.

**Alternatives considered**: Motor 3.7.1 — rejected, deprecated. Sync PyMongo behind a thread pool —
rejected: it reintroduces thread-pool sizing as a scaling variable and defeats the point of an async
ingestion path.

**Verified**: PyPI metadata read 2026-08-31.

---

## R2: Queue abstraction with two adapters

**Decision**: One `EventQueue` port; two implementations. `InMemoryQueue` (an `asyncio.Queue` with a
bounded capacity, visibility timeout, redelivery, and attempt counting) is the default and the one
the test suite drives. `RabbitMQQueue` (aio-pika 10.0.1, quorum queues) is what Docker Compose runs.

**Rationale**: The assignment asks for a simulated in-process queue *and* asks what would change
with real SQS. A port with two live implementations answers the second question by demonstration
rather than assertion — SQS becomes a visibly third adapter, and the domain never learns which is
mounted. Keeping the in-process adapter as the test default satisfies constitution X.3 (tests must
not require external production services) and keeps the suite runnable with one command.

**Alternatives considered**: In-process only — rejected: the swap-an-adapter claim stays untested.
RabbitMQ only — rejected: every integration test would need a broker, and the assignment's stated
in-process queue would be gone. Kafka — rejected: partition and consumer-group semantics are a much
larger topic than the assignment's at-least-once framing needs.

---

## R3: Retry with backoff on a broker that has no native backoff

**Decision**: The retry schedule lives in `domain/policies/retry.py` as a pure function of attempt
number: `min(base * 2**(n-1), cap)` with full jitter, default base 1 s, cap 60 s, five attempts.
The in-process adapter implements it with a delayed re-enqueue. The RabbitMQ adapter implements it
with a ladder of retry queues that have a per-queue message TTL and no consumer, dead-lettering back
to the work exchange when the TTL expires.

**Rationale**: RabbitMQ has no delayed redelivery primitive. `nack(requeue=true)` redelivers
immediately, which turns a failing message into a hot loop that saturates the worker and the broker.
The TTL-plus-dead-letter-exchange ladder is the standard workaround. Keeping the schedule itself in
the domain as a pure function means it is unit-testable without any broker and is identical across
adapters — which is what makes the retry claim in `ARCHITECTURE.md` provable.

**Alternatives considered**: `rabbitmq_delayed_message_exchange` plugin — rejected: a non-core
plugin adds an image build step to Compose. Retrying inside the worker with `asyncio.sleep` —
rejected: an in-flight sleep holds the message and its prefetch slot, so a slow dependency blocks
throughput for healthy messages.

---

## R4: Idempotency enforced by the storage engine

**Decision**: A unique index on `(tenant_id, event_id)`. The worker issues `insert_one` and treats
`DuplicateKeyError` as success-by-suppression, incrementing a `duplicate_suppressed` counter. To
distinguish a benign duplicate from a conflicting one (FR-014), the stored document carries a
`content_hash` of the canonical envelope; on a duplicate key, the worker compares hashes, and a
mismatch is recorded as `duplicate_conflict` with the first-stored version kept.

**Rationale**: Constitution III.5 requires the constraint to be enforced by the store, not by
application logic. A read-then-write check loses the race between two workers processing the same
redelivered message; a unique index cannot. Deriving conflict detection from a hash rather than a
field-by-field comparison keeps the check O(1) and stable across field ordering.

**Alternatives considered**: Application-level "does it exist?" check — rejected: races. Upsert with
`$setOnInsert` — rejected: it succeeds silently on a conflicting duplicate, losing FR-014's signal.
A Redis-based dedup set — rejected: it makes a derived, evictable store load-bearing for
correctness, violating Principle IV.

---

## R5: Projection reliability — status field plus reconciler

**Decision**: The event document carries `projection: {status, attempts, last_error, updated_at}`.
The worker inserts the canonical event, acknowledges the queue message, then attempts the
Elasticsearch projection and updates the status field. A background reconciler periodically scans
for `status: pending` documents older than a threshold and retries them.

**Rationale**: This is the smallest construct that satisfies both Principle IV.4 (an indexing
failure must not discard a persisted event) and IV.7 (projection failures observable and retryable).
Because the status lives on the event document itself, MongoDB's single-document atomicity gives the
write and its projection state a consistent update with no transaction and therefore no replica set.
The reconciler makes "search catches up after an Elasticsearch outage" a demonstrable behaviour
rather than a hope.

**Alternatives considered**: A true transactional outbox in a separate collection with a change
stream reader — this is the correct production answer and is documented as the evolution path in
ADR-005, but it requires a replica set and a second moving part for no additional guarantee at this
scale. Dual-write with no status tracking — rejected: events written during an Elasticsearch outage
are permanently missing from search with no way to find them. Indexing before acknowledging —
rejected: it makes a derived store's availability gate canonical persistence, inverting Principle IV.

---

## R6: Cache-aside with bounded staleness and single-flight

**Decision**: Key shape `v{schema}:{tenant_id}:stats:rt:{sha256(normalized_filters)[:16]}`, TTL 30
seconds, no write-path invalidation. Concurrent misses for the same key are collapsed by a
short-lived Redis lock so only one request recomputes (single-flight); the others wait briefly and
read the filled value. On any Redis error the request computes from MongoDB and returns with
`degraded: true`.

**Rationale**: Under a write-heavy event stream, invalidating on write means invalidating
continuously, which is a cache that never serves. Bounded staleness is the honest trade: 30 seconds
is short enough that a dashboard feels live and long enough that a 1,000-events/second stream is
absorbed by one recomputation per key per window. Hashing *normalized* filters into the key is what
makes FR-028 hold — the tenant and every filter dimension are inside the key, so no two distinct
requests can share an entry. Single-flight exists because the obvious failure of cache-aside under
load is the stampede at expiry, not the miss itself.

**Alternatives considered**: Write-through — rejected: every event write would touch every affected
summary key, and the fan-out is unbounded in the filter dimensions. Write-behind — rejected: it puts
durable business state in a derived store, against Principle IV.3. Invalidate-on-write — rejected as
above. No TTL with explicit invalidation only — rejected: any missed invalidation is permanent
staleness with no self-correction.

---

## R7: Elasticsearch mapping — explicit envelope, `flattened` metadata

**Decision**: `dynamic: strict` on the index. Envelope fields are mapped explicitly: `tenant_id`,
`event_id`, `event_type`, `anonymous_id`, `contact_id`, `session_id`, `campaign_id`, `channel`,
`conversion.goal_id` as `keyword`; `occurred_at`, `received_at` as `date`; `conversion.value` as
`scaled_float`; `source_url` as `keyword` with a `text` sub-field. Customer metadata maps to a
single `flattened` field, plus a `metadata_text` field that receives an analyzed copy of an
allowlisted subset for full-text search.

**Rationale**: `flattened` is the field type designed for exactly this problem — it indexes an
entire JSON object as one field, so a customer inventing a thousand metadata keys creates one
mapping entry, not a thousand. That is what makes Principle VII.7 ("dynamic mapping of arbitrary
customer metadata SHOULD be restricted or disabled") achievable without rejecting customer data.
`dynamic: strict` means an unmapped field is a loud error at index time rather than a silent mapping
addition. Exact-match dimensions are `keyword` because they are filtered and aggregated, never
tokenized (Principle VII.5).

**Alternatives considered**: `dynamic: false` with metadata stored but unindexed — rejected: it
kills the metadata search the assignment requires. `dynamic: true` — rejected: mapping explosion,
which is the failure mode the constitution names. Flattening metadata into `key=value` strings at
write time — rejected: it loses type information and makes range queries impossible.

**Trade-off to state plainly in the architecture document**: `flattened` indexes every value as a
keyword. Range queries and analyzed search on individual metadata fields are not available inside
it; that is why the allowlisted `metadata_text` copy exists alongside it.

---

## R8: Tenant resolution from an opaque credential

**Decision**: The caller sends `Authorization: Bearer <api_key>`. The key is stored only as a
SHA-256 hash in a `tenants` collection alongside its `tenant_id` and status. A FastAPI dependency
resolves the hash to a `TenantContext` and injects it; every repository, search, and cache call
takes that context as a required argument rather than reading it from ambient state.

**Rationale**: Implements spec decision D1. Making the context a required parameter on every port
method means a missing tenant scope is a type error at authoring time, not a data leak at runtime —
the strongest available structural defence for Principle I.3. Hashing at rest means a leaked
database dump does not yield working credentials.

**Alternatives considered**: `X-Tenant-Id` header — rejected in D1: spoofable, so the isolation
tests would prove scoping but not authorization. JWT with a tenant claim — rejected: adds key
management and token minting for no additional evidence at review scale; noted in ADR-004 as the
production direction. Ambient context via `contextvars` — rejected: it makes forgetting the scope
possible again, which is the entire risk being designed against.

---

## R9: Aggregation pipeline and time bucketing

**Decision**: `$match` on `tenant_id` and an `occurred_at` range first, then `$group` on
`{type: "$event_type", bucket: {$dateTrunc: {date: "$occurred_at", unit: <hour|day|week>}}}`, then
`$sort`. Empty buckets are filled in the application layer, not the pipeline.

**Rationale**: `$dateTrunc` is a single server-side operation that maps directly onto the three
required granularities and is timezone-correct, unlike composing `$year`/`$month`/`$day`. Putting
`$match` first with a leading `tenant_id` lets the compound index serve the pipeline rather than
scanning. Filling empty buckets in application code keeps FR-022's answer honest (an absent bucket
is rendered as zero, not omitted, per US3 scenario 4) without a `$densify` stage that would cost
memory on wide ranges.

**Alternatives considered**: `$densify` — rejected: unnecessary server-side work for a formatting
concern. Pre-aggregated rollup collections updated by the worker — the right answer at 10x volume
and named as such in the scaling section, but premature here and it would put derived counts in the
canonical store.

---

## R10: MongoDB index set, and what is deliberately absent

**Decision** — five compound indexes, each led by `tenant_id`:

| Index | Serves |
|---|---|
| `{tenant_id: 1, event_id: 1}` unique | Idempotency (R4), FR-011, FR-012 |
| `{tenant_id: 1, occurred_at: -1}` | Default listing and every bounded-range aggregation |
| `{tenant_id: 1, event_type: 1, occurred_at: -1}` | Type filter plus range; the stats pipeline |
| `{tenant_id: 1, contact_id: 1, occurred_at: -1}` sparse | Known-identity lookups |
| `{tenant_id: 1, anonymous_id: 1, occurred_at: -1}` sparse | Anonymous-visitor lookups |

**Deliberately omitted, with reasons** (Principle VII.9):

- **`source_url`** — high cardinality and low selectivity within a tenant; most queries pair it with
  a type or range filter that an existing index already serves. The write cost of a sixth index on
  every insert is not repaid. Revisit if URL-only queries appear in real traffic.
- **`campaign_id`** — same argument at present volume; it is the first index to add when
  campaign-scoped reporting becomes a first-class access pattern.
- **Any index on `metadata.*`** — metadata is schemaless by contract, so indexing it in MongoDB
  means either an unbounded set of indexes or a wildcard index whose selectivity is unpredictable.
  Metadata search belongs to Elasticsearch; that division is the whole justification for having
  Elasticsearch in the design.
- **A TTL index for retention** — retention is out of scope (spec Assumptions) and a TTL index would
  quietly delete canonical events, contradicting Principle II's immutability.

---

## R11: Keyset pagination, not skip/limit

**Decision**: Listings paginate on an opaque cursor encoding `(occurred_at, event_id)` of the last
row, with a default page size of 50 and a hard maximum of 200.

**Rationale**: `skip` costs time linear in the offset, so deep pages degrade exactly when a customer
is exporting the most data. A composite cursor on the index's own sort key is O(1) per page and, by
including `event_id`, gives the total ordering FR-021 requires when many events share a timestamp —
which they will, since event timestamps cluster. The cursor is opaque so its shape can change
without breaking clients.

**Alternatives considered**: `skip`/`limit` — rejected on deep-page cost. Page numbers over a cached
result set — rejected: it makes a derived store load-bearing for a canonical read.

---

## R12: Explicit backpressure

**Decision**: The queue has a configured capacity. When a publish would exceed it — or when the
broker signals flow control — the API returns `503` with a `Retry-After` header and increments
`ingest_rejected_backpressure`. The publish call is wrapped in a timeout so a blocked broker becomes
a fast rejection rather than a hanging request. A token-bucket rate limiter in front of ingestion
sheds abusive load before it reaches validation.

**Rationale**: Principle VI.6 requires the API to reject rather than silently lose. The publish
timeout is the specific defence against RabbitMQ's memory-alarm flow control, which blocks
publishers: without a timeout, a broker slowdown turns `POST /events` into a hanging request and a
broker problem becomes an API outage.

**Alternatives considered**: Unbounded queue — rejected: it converts backpressure into an
out-of-memory crash, which is silent event loss. Blocking the caller until capacity frees —
rejected: it propagates the stall to the sender's page render, defeating the async design.

---

## R13: Documentation toolchain

**Decision**: Mermaid fenced blocks inside Markdown for all four diagrams; Nygard-form ADRs; arc42
section order per constitution IX.

**Rationale**: Mermaid renders natively on GitHub, so a reviewer sees the diagram in the browser
with no build step, and it diffs as text in a pull request. Structurizr would give a single model
generating multiple views, but it adds a toolchain a reviewer must install to see anything.

**Alternatives considered**: Structurizr DSL, PlantUML, committed PNGs — the last is rejected
outright: an image cannot be reviewed in a diff, which defeats docs-as-code.

---

## R14: Python 3.13 with 3.14 available locally

**Decision**: Target 3.13; pin it in `pyproject.toml`, the Compose image, and CI, even though the
development machine has 3.14.5.

**Rationale**: Constitution XI.3 names 3.13 as the target. More practically, the third-party
matrix — Elasticsearch, Redis, aio-pika, Testcontainers — has had longer to settle on 3.13, and a
take-home should not spend its reviewer's goodwill on a wheel that fails to build. Pinning the same
version locally, in the image, and in CI removes "works on my machine" as an explanation for any
failure a reviewer hits.

**Alternatives considered**: 3.14 — no feature this design needs is exclusive to it. 3.12 —
unnecessarily conservative.

---

## R15: Event time validation

**Decision**: `occurred_at` is required and must be within a configurable window: no more than 5
minutes in the future (clock skew tolerance) and no more than 90 days in the past. Values outside
the window are rejected with a field-specific error. `received_at` is always server-assigned and is
never taken from the client.

**Rationale**: Unbounded `occurred_at` lets one misconfigured client rewrite historical aggregates —
a correctness failure that surfaces as an inexplicable spike in a report weeks later. A bounded
window makes the aggregate range queries in R9 meaningful and keeps FR-023's bounded ranges honest.
Server-assigned `received_at` is what makes the ingestion-lag measurement in SC-011 trustworthy;
a client-supplied value could hide lag.

**Alternatives considered**: Accept anything and flag it — rejected: the bad data is already in the
aggregate by the time anyone reads the flag. Clamp to now — rejected: it silently falsifies an event
timestamp, which is a worse failure than a rejection the sender can see and fix.

---

## Scaling analysis: what breaks first at 10x

Recorded here so `ARCHITECTURE.md` section 12 is assembled from analysis rather than written fresh.
At 10,000 events/second and 100M stored events, in the order the failures actually arrive:

1. **Elasticsearch indexing throughput**, first. One-document-per-event indexing is the most
   expensive operation per event in the pipeline. Fix: bulk the projection in the worker
   (size-and-time-bounded batches), then move projection to a dedicated consumer group.
2. **MongoDB write amplification from five indexes**, second. Every insert updates all five B-trees.
   Fix: shard on `tenant_id` — which the leading-`tenant_id` index design already prepares for — and
   move the two identity indexes behind a documented access-pattern review.
3. **The aggregation pipeline**, third. Scanning a month of events per dashboard load stops being
   viable. Fix: pre-aggregated rollups maintained by the worker, with the raw pipeline retained for
   backfill and correction.
4. **Redis single-key contention** on popular summaries, fourth. Fix: the single-flight lock from R6
   already blunts it; beyond that, shard summary keys by time bucket so writers do not converge.
5. **The single worker process**, throughout. Horizontal worker scaling is the first lever and is
   already available: the design's idempotency means adding consumers is safe by construction.

The honest headline for the failure-modes document: with the in-process queue adapter, a process
restart loses queued-but-unprocessed events. That is not a bug to be excused, it is the reason the
RabbitMQ adapter exists and the reason ADR-007 exists.
