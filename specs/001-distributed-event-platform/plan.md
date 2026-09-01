# Implementation Plan: Multi-Tenant Distributed Event Processing Platform

**Branch**: `CORE-001` (spec directory `001-distributed-event-platform`) | **Date**: 2026-08-31 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/001-distributed-event-platform/spec.md`

**Governing constitution**: `.specify/memory/constitution.md` v1.1.0

**Documentation research input**: `docs/research.md` (arc42-lite, C4-via-Mermaid, Nygard ADRs,
recommended repository layout). Note: that file is *input*. The Phase 0 output of this command is
the separate file [research.md](./research.md) in this directory — the two are not the same
document and neither overwrites the other.

## Summary

Build a multi-tenant event platform as a modular monolith with hexagonal boundaries. Senders submit
events over HTTP; the API validates, resolves the tenant from an opaque credential, and hands the
event to a queue, returning `202 Accepted` without waiting for storage. A worker consumes the
queue, writes the canonical record to MongoDB idempotently under a unique `(tenant_id, event_id)`
index, then projects to Elasticsearch for metadata search. Reads are served three ways: filtered
listing and time-bucketed aggregation from MongoDB, free-text metadata search from Elasticsearch,
and a low-latency live summary from Redis using cache-aside with bounded staleness and a
compute-from-MongoDB degraded fallback.

The architecture document is the principal deliverable. `ARCHITECTURE.md` follows the arc42-lite
section order fixed by constitution Principle IX, is illustrated by four Mermaid diagrams
(C4 context, C4 container, component, ingest/failure sequence), and is backed by seven Nygard-form
ADRs. Every reliability and isolation claim it makes is traceable to a named automated test, a
named metric, or an explicitly documented limitation (SC-016).

Two verified findings shape the stack. First, Motor passed its deprecation date on 2026-05-14, so
the async MongoDB driver is PyMongo 4.17's native `AsyncMongoClient`, not Motor. Second,
Elasticsearch's `flattened` field type solves the mapping-explosion problem for arbitrary customer
metadata that constitution Principle VII.7 requires us to bound. Both are recorded in
[research.md](./research.md) with the alternatives rejected.

## Technical Context

**Language/Version**: Python 3.13 (constitution XI.3). Container base `python:3.13-slim`. The
development machine runs 3.14.5; the toolchain pins 3.13 so local runs and CI match the image.

**Primary Dependencies** (versions verified against PyPI on 2026-08-31, constitution XI.2):
FastAPI 0.141.1, Pydantic 2.13.5, Uvicorn 0.52.4, PyMongo 4.17.0 (native async client),
elasticsearch 9.5.0, redis 8.1.0, aio-pika 10.0.1, structlog 26.1.0, httpx 0.28.1.

**Storage**: MongoDB 8.x canonical event store and dead-letter store; Elasticsearch 9.x derived
search projection; Redis 8.x derived cache. RabbitMQ 4.x as the durable queue adapter in Compose;
an in-process queue adapter is the default and is what the test suite drives.

**Testing**: pytest 9.1.1, pytest-asyncio 1.4.0, testcontainers 4.15.0. Unit tests need no
services. Integration tests bring up real MongoDB, Elasticsearch, and Redis via Testcontainers and
drive the in-process queue adapter, so the suite runs with one command and no broker.

**Target Platform**: Linux containers orchestrated by Docker Compose; a single reviewable
environment, not a multi-region deployment.

**Project Type**: Backend web service — modular monolith, hexagonal boundaries, no frontend.

**Performance Goals** (from spec Success Criteria): accept or reject in under 100 ms p95 at
1,000 events/second (SC-001); first page of a filtered 30-day listing under 500 ms p95 at 10M
stored events (SC-006); 30-day grouped counts under 2 s p95 (SC-007); repeated live summary under
20 ms p95 with a 95% served-from-cache rate (SC-008); 95% of events searchable within 5 s (SC-011).

**Constraints**: `202 Accepted` must never imply persistence (FR-002); the queue must not be
acknowledged before canonical persistence (FR-015); tenant scoping applies to every read,
aggregate, search, and cache key (FR-033, FR-028); no raw Elasticsearch DSL from clients (FR-025);
metadata must not expand the searchable field set without bound (FR-026); Redis unavailability must
degrade rather than fail (FR-030); Elasticsearch unavailability must not lose a stored event
(FR-038).

**Scale/Scope**: 10M stored events, 1,000 events/second sustained intake, tens of tenants, six
user stories, 44 functional requirements, 16 success criteria.

## Constitution Check

*GATE: evaluated before Phase 0 and re-evaluated after Phase 1 design. Both passes recorded.*

| # | Principle | Gate | Pre-Phase-0 | Post-Phase-1 | How the design satisfies it |
|---|-----------|------|-------------|--------------|------------------------------|
| I | Tenant isolation | Every store, query, index, and cache key is tenant-scoped | PASS | PASS | `tenant_id` leads every MongoDB index and the unique constraint; Elasticsearch queries wrap user terms in a `filter` on `tenant_id`; Redis keys are `v1:{tenant_id}:...`; tenant comes from a credential the caller cannot forge (D1) |
| II | Immutable versioned events | Envelope carries required fields; no mutation path | PASS | PASS | Envelope fixed in [data-model.md](./data-model.md); only `insert` and `find` on the events collection — no update or delete operation is exposed or implemented; `schema_version` present from v1 |
| III | At-least-once + idempotency | Duplicate-safe, ack-after-persist, bounded retry, DLQ | PASS | PASS | Unique `(tenant_id, event_id)` index makes duplicate suppression a storage guarantee, not application logic; ack strictly after insert confirms; exponential backoff with jitter; dead-letter store with failure metadata |
| IV | Mongo canonical, others derived | Projections disposable and rebuildable | PASS | PASS | Elasticsearch index and Redis keys are both rebuildable from MongoDB; projection state lives on the event document; a reconciler retries pending projections; both derived stores can be dropped and rebuilt |
| V | Modular monolith, hexagonal | Domain imports no infrastructure | PASS | PASS | Layout below; an import-linter contract fails the build if `domain/` or `application/` imports FastAPI, PyMongo, redis, elasticsearch, aio-pika, or boto3 — the boundary is enforced by a test, not by convention |
| VI | Reliability and operability | Structured logs, signals, backpressure, failure docs | PASS | PASS | structlog with request/event/tenant/worker correlation; every Principle VI.4 signal has a named counter; bounded queue returns `503` with `Retry-After`; `docs/failure-modes.md` |
| VII | Designed query performance | Indexes from access patterns; bounded queries | PASS | PASS | Five compound indexes derived from FR-020 filters; keyset pagination rather than skip; `$dateTrunc` bucketing; explicit Elasticsearch mappings with `flattened` metadata; deliberate omissions documented |
| VIII | Secure by default | Validation, limits, no raw DSL, secrets from env | PASS | PASS | Pydantic validation with payload/depth/key-count limits; search built from structured parameters only; credential hashed at rest; `docs/threat-model.md` STRIDE-lite |
| IX | Documentation first-class | arc42-lite, four diagrams, seven ADRs, committed OpenAPI | PASS | PASS | Documentation Architecture section below maps every required document to its content source |
| X | Tests prove claims | Named unit and integration coverage, one command | PASS | PASS | Test plan in [quickstart.md](./quickstart.md) maps all seven required integration paths plus the boundary test to the success criteria they prove |
| XI | Delivery quality | Lockfile, current versions, Compose, lint/type/CI | PASS | PASS | uv lockfile; versions verified today; Compose with health-gated startup; ruff, mypy, import-linter, pytest in CI |

**Result**: no unjustified violations. Two deliberate additions to the assignment's minimum are
recorded in Complexity Tracking with their justification.

## Project Structure

### Documentation (this feature)

```text
specs/001-distributed-event-platform/
├── plan.md              # This file
├── research.md          # Phase 0 output: decisions, rationale, alternatives rejected
├── data-model.md        # Phase 1 output: entities, collections, indexes, mappings, keys
├── quickstart.md        # Phase 1 output: run it, prove it, test-to-criterion map
├── contracts/           # Phase 1 output
│   ├── openapi.yaml     #   HTTP contract
│   ├── queue-message.md #   queue message envelope and delivery semantics
│   └── ports.md         #   the eight port interfaces the domain depends on
├── checklists/
│   └── requirements.md  # Spec quality checklist (16/16 pass)
└── tasks.md             # Phase 2 output — created by /speckit-tasks, not by this command
```

### Repository documentation (implementation deliverables)

Created during implementation, structure fixed here so tasks can be generated against it. This is
the layout recommended by `docs/research.md` reconciled with constitution Principle IX.

```text
.
├── README.md                          # evaluator-facing: run in 10 minutes, AI-in-workflow
├── ARCHITECTURE.md                    # PRINCIPAL DELIVERABLE — arc42-lite, 13 sections
├── docs/
│   ├── diagrams/
│   │   ├── system-context.md          # C4 L1 (Mermaid)
│   │   ├── container.md               # C4 L2 (Mermaid)
│   │   ├── component.md               # C4 L3 (Mermaid)
│   │   └── ingest-sequence.md         # success + failure/retry sequence (Mermaid)
│   ├── adr/
│   │   ├── ADR-001-modular-monolith.md
│   │   ├── ADR-002-at-least-once-idempotency.md
│   │   ├── ADR-003-storage-responsibilities.md
│   │   ├── ADR-004-tenant-isolation.md
│   │   ├── ADR-005-projection-reliability-and-outbox.md
│   │   ├── ADR-006-cache-aside-bounded-staleness.md
│   │   └── ADR-007-queue-migration-to-sqs.md
│   ├── event-contract.md
│   ├── data-model.md
│   ├── quality-scenarios.md
│   ├── failure-modes.md
│   ├── observability.md
│   └── threat-model.md
├── openapi/openapi.yaml               # committed, diff-reviewable
├── docker-compose.yml
└── src/, tests/
```

### Source code (repository root)

```text
src/eventplatform/
├── domain/                       # no infrastructure imports, enforced by import-linter
│   ├── event.py                  # Event, EventEnvelope, Identity, Attribution, Consent
│   ├── event_type.py             # event-type catalog and validation
│   ├── metadata.py               # metadata size/depth/key-count/type rules
│   ├── errors.py                 # ValidationError, DuplicateEvent, ConflictingDuplicate
│   └── policies/
│       ├── idempotency.py        # what makes two deliveries the same event
│       ├── retry.py              # attempt schedule, exponential backoff with jitter
│       ├── cache_key.py          # deterministic tenant + filter cache identity
│       └── time_window.py        # range validation, clamping, bucket rules
├── application/                  # use cases; depends on ports only
│   ├── ingest_event.py           # validate -> resolve tenant -> enqueue -> receipt
│   ├── process_event.py          # idempotent persist -> project -> ack
│   ├── query_events.py           # filtered, paginated, tenant-scoped listing
│   ├── aggregate_stats.py        # bucketed counts over a bounded range
│   ├── search_events.py          # structured query construction
│   ├── realtime_stats.py         # cache-aside with degraded fallback
│   └── replay_dead_letter.py     # operator replay of exhausted events
├── ports/
│   ├── event_queue.py            ├── event_repository.py    ├── event_search_index.py
│   ├── stats_cache.py            ├── dead_letter_store.py   ├── tenant_directory.py
│   ├── clock.py                  └── metrics.py
├── infrastructure/
│   ├── queue/{in_memory.py, rabbitmq.py}
│   ├── persistence/mongo/{event_repository.py, indexes.py, dead_letter_store.py, tenant_directory.py}
│   ├── search/elasticsearch/{index.py, mappings.py, query_builder.py}
│   ├── cache/redis/{stats_cache.py, keys.py, single_flight.py}
│   ├── observability/{logging.py, metrics.py, correlation.py}
│   └── config/settings.py        # environment-sourced, no defaults that hide misconfiguration
├── api/
│   ├── routes/{events.py, stats.py, search.py, health.py, dead_letters.py}
│   ├── schemas/{requests.py, responses.py, errors.py}
│   ├── middleware/{correlation.py, rate_limit.py}
│   ├── dependencies.py           # tenant resolution from credential
│   └── errors.py                 # domain error -> HTTP status mapping
├── worker/{consumer.py, projector.py, reconciler.py}
└── main.py

tests/
├── unit/                         # domain policies, validation, cache keys, error mapping
├── integration/                  # the seven required end-to-end paths
├── contract/                     # OpenAPI conformance, queue message shape
└── architecture/                 # import-linter boundary contract
```

**Structure Decision**: single-project modular monolith. The four layers are directories, not
services, and the dependency rule points inward: `api` and `worker` depend on `application`,
`application` depends on `ports`, `infrastructure` implements `ports`, and `domain` depends on
nothing. This is what makes the SQS migration in ADR-007 credible — it lands entirely inside
`infrastructure/queue/` as a third adapter next to the two that already exist. Microservices were
rejected per constitution Principle V.7: the assignment demands separate *concerns*, and
directories with an enforced dependency rule deliver that without networking, deployment, and
distributed-transaction overhead.

## Documentation Architecture

`ARCHITECTURE.md` is the principal deliverable. Its arc42-lite sections are fixed by constitution
Principle IX; the table below binds each to the artifact that supplies its content, so the document
is assembled from work already done rather than written from memory.

| arc42 section | Content source | Assignment requirement served |
|---|---|---|
| 1. Executive Summary | spec Summary, this plan's Summary | orientation |
| 2. Business Context and Assumptions | spec Assumptions, Resolved Decisions D1/D2 | product context |
| 3. Goals and Quality Attributes | spec Success Criteria SC-001..016 | — |
| 4. Constraints | Technical Context above | tech stack |
| 5. System Context | `docs/diagrams/system-context.md` | **system diagram** |
| 6. Solution Strategy | research.md decisions R1..R15 | storage rationale |
| 7. Container and Component Views | `docs/diagrams/container.md`, `component.md` | **component responsibilities** |
| 8. Runtime Scenarios | `docs/diagrams/ingest-sequence.md`, failure-modes.md | **failure modes**, queue guarantees |
| 9. Data Architecture | `docs/data-model.md`, `docs/event-contract.md` | **indexing strategy**, ES mappings |
| 10. Cross-Cutting Concerns | `docs/observability.md`, `docs/threat-model.md` | caching strategy, isolation |
| 11. Deployment View | `docker-compose.yml`, quickstart.md | bonus: Compose |
| 12. Risks, Scaling, Future Evolution | research.md 10x analysis, ADR-007 | **scaling**, **what I'd do differently** |
| 13. ADR Index | `docs/adr/` | trade-off reasoning |

Two assignment requirements are answered in more than one place by design, because the panel will
look for them where they expect them: the queue-guarantees discussion appears in section 8 and in
ADR-002/ADR-007; the "what changes with real SQS" answer appears in section 12 and in ADR-007.

## Complexity Tracking

Two departures from the simplest reading of the assignment. Both are deliberate and both are
justified rather than hidden.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|--------------------------------------|
| Two queue adapters (in-process **and** RabbitMQ) rather than one | The assignment asks for a simulated in-process queue and asks what would change with real SQS. Running a real broker behind the same port turns that answer from a claim into a demonstration: the port already has two implementations, so SQS is visibly a third. The in-process adapter remains the default and is what the test suite drives, so the literal requirement is met and the suite needs no broker. | A single in-process queue leaves "we could swap the adapter" untested. A RabbitMQ-only design breaks constitution X.3 by making every integration test require a broker, and abandons the assignment's stated in-process queue. |
| A projection reconciler in addition to inline projection | Constitution IV.7 requires projection failures to be observable and retryable, and FR-038 requires a stored event to stay retrievable when its projection fails. Inline projection alone silently loses the indexing attempt when Elasticsearch is down mid-write. The reconciler is roughly forty lines scanning a `projection.status` field. | Fire-and-forget projection makes the search index permanently miss events written during an Elasticsearch outage, with no path back. A full transactional outbox with a separate collection and change streams is the production answer, documented in ADR-005, but its operational weight is not justified inside a one-week take-home. |

One simplification is worth naming here because it contradicts an earlier working assumption in
this repository: MongoDB runs as a **standalone node, not a replica set**. Change streams and
multi-document transactions both require a replica set, and an earlier draft assumed we would need
them. This design needs neither — idempotency rests on a unique index and single-document atomicity,
and projection state lives on the event document itself. Dropping the replica set removes an
init container and a class of startup race from Compose. The trade-off is recorded in ADR-005: a
production outbox reader would want change streams, and that is where the replica set returns.
