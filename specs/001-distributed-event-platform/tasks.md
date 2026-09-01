---
description: "Task list for the Multi-Tenant Distributed Event Processing Platform"
---

# Tasks: Multi-Tenant Distributed Event Processing Platform

**Input**: Design documents from `specs/001-distributed-event-platform/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/), [quickstart.md](./quickstart.md)

**Tests**: INCLUDED and mandatory. Constitution Principle X requires named tests for the reliability
and isolation claims, and SC-016 requires every documented guarantee to map to a test, a metric, or
a stated limitation. The 21-row map in [quickstart.md](./quickstart.md) is the authority; each row
appears below as a task and is cross-referenced as `[map #N]`.

**Organization**: Grouped by user story so each is independently implementable and testable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: `[US1]`..`[US6]` maps to the spec's user stories
- Exact file paths in every task

## Path Conventions

Single project, modular monolith. Source under `src/eventplatform/`, tests under `tests/`, both at
repository root, per plan.md Structure Decision.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: A runnable, lintable, type-checked skeleton with every dependency pinned.

- [X] T001 Create the source and test tree from plan.md: `src/eventplatform/{domain,application,ports,infrastructure,api,worker}/` and `tests/{unit,integration,contract,architecture}/`, each with `__init__.py`
- [X] T002 Initialize the uv project in `pyproject.toml` targeting Python 3.13, pinning fastapi 0.141.1, pydantic 2.13.5, uvicorn 0.52.4, pymongo 4.17.0, elasticsearch 9.5.0, redis 8.1.0, aio-pika 10.0.1, structlog 26.1.0, httpx 0.28.1, and generate `uv.lock` (research.md R1 — PyMongo native async, NOT Motor, which is past its 2026-05-14 deprecation)
- [X] T003 [P] Configure ruff and mypy strict mode in `pyproject.toml`
- [X] T004 [P] Configure the import-linter boundary contract in `.importlinter`: `domain` and `application` may not import fastapi, pymongo, redis, elasticsearch, aio_pika, or boto3
- [X] T005 [P] Configure pytest in `pyproject.toml`: `asyncio_mode = "auto"`, markers `rabbitmq` and `slow`, and testpaths
- [X] T006 [P] Write `.env.example` covering every setting name with safe non-secret defaults
- [X] T007 Write the multi-stage `Dockerfile` on `python:3.13-slim` with a non-root runtime user
- [X] T008 Write `docker-compose.yml` for api, worker, mongodb, elasticsearch, redis, rabbitmq — every dependency gated by `condition: service_healthy`, MongoDB standalone with NO replica set (plan.md Complexity Tracking), Elasticsearch `Xms` equal to `Xmx` and under half the container memory limit
- [X] T009 [P] Write `.github/workflows/ci.yml` running ruff, mypy, lint-imports, unit tests, and integration tests
- [X] T010 [P] Add `.DS_Store`, `.env`, `.venv`, and `__pycache__` to `.gitignore`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The domain core, the ports, and the cross-cutting infrastructure every story needs.

**⚠️ CRITICAL**: No user story work begins until this phase completes.

- [X] T011 Implement environment-sourced settings in `src/eventplatform/infrastructure/config/settings.py` — no default that hides a misconfiguration; fail loudly on a missing required value
- [X] T012 [P] Configure structlog JSON logging in `src/eventplatform/infrastructure/observability/logging.py` with a payload/PII redaction processor (FR-036)
- [X] T013 [P] Implement correlation-id propagation (request, event, tenant, worker) in `src/eventplatform/infrastructure/observability/correlation.py`
- [X] T014 [P] Implement the Prometheus metrics adapter in `src/eventplatform/infrastructure/observability/metrics.py` with the bounded label sets from contracts/ports.md
- [X] T015 [P] Define the domain error hierarchy in `src/eventplatform/domain/errors.py`: `ValidationError`, `ConflictingDuplicate`, `CacheUnavailable`, `SearchUnavailable`, `QueueAtCapacity`
- [X] T016 [P] Define the twelve-entry event-type catalog in `src/eventplatform/domain/event_type.py` (data-model.md)
- [X] T017 [P] Implement metadata limits in `src/eventplatform/domain/metadata.py`: ≤16 KB serialized, ≤3 levels deep, ≤50 keys, scalar/array/object values only (FR-004)
- [X] T018 Implement the event envelope in `src/eventplatform/domain/event.py` — `Event`, `Identity`, `Attribution`, `Consent`, and `content_hash` as SHA-256 over the canonical envelope (data-model.md, FR-043)
- [X] T019 [P] Implement the retry schedule as a pure function in `src/eventplatform/domain/policies/retry.py`: `min(1s * 2**(n-1), 60s)` with full jitter, 5 attempts (research.md R3)
- [X] T020 [P] Implement deterministic cache-key construction in `src/eventplatform/domain/policies/cache_key.py`: tenant as a literal segment, SHA-256 over normalized filters (research.md R6, FR-028)
- [X] T021 [P] Implement range and bucket rules in `src/eventplatform/domain/policies/time_window.py`: `occurred_at` within −90 days/+5 minutes, maximum aggregate span, hour/day/week buckets (research.md R15, FR-023)
- [X] T022 [P] Implement the idempotency decision and `InsertOutcome` enum in `src/eventplatform/domain/policies/idempotency.py` (contracts/ports.md)
- [X] T023 Define all eight port Protocols in `src/eventplatform/ports/` — every tenant-touching method takes `tenant: TenantContext` as a required first argument; `EventRepository` exposes NO general update and NO delete (contracts/ports.md, FR-010)
- [X] T024 Implement the MongoDB client and index creation in `src/eventplatform/infrastructure/persistence/mongo/indexes.py` — the five indexes of research.md R10, all led by `tenant_id`, with `(tenant_id, event_id)` unique
- [X] T025 Implement `TenantContext` and the tenant directory adapter in `src/eventplatform/infrastructure/persistence/mongo/tenant_directory.py` — SHA-256 key hashes, unknown and revoked resolving identically (research.md R8, FR-034)
- [X] T026 Implement the `issue-key` command in `src/eventplatform/cli.py` so a reviewer can mint a tenant credential (quickstart.md)
- [X] T027 Implement the FastAPI app factory and correlation middleware in `src/eventplatform/api/main.py` and `src/eventplatform/api/middleware/correlation.py`
- [X] T028 Implement domain-error to RFC 9457 mapping in `src/eventplatform/api/errors.py` (contracts/openapi.yaml `Problem`)
- [X] T029 Implement credential-to-tenant resolution in `src/eventplatform/api/dependencies.py` — bearer token to `TenantContext`, rejecting absent, unknown, and revoked identically (FR-032)
- [X] T030 [P] Implement `/healthz` and `/readyz` in `src/eventplatform/api/routes/health.py` — liveness distinct from readiness, readiness naming the failing dependency (FR-041)
- [X] T031 [P] Expose `/metrics` in `src/eventplatform/api/routes/health.py`
- [X] T032 [P] Unit-test the domain policies in `tests/unit/test_policies.py`: `test_retry_schedule_backoff_jitter` [map #16], `test_occurred_at_window` [map #15], `test_cache_key_never_shared_across_tenants` [map #10], `test_metadata_limits_rejected` [map #14]
- [X] T033 [P] Write the boundary test `test_domain_imports_no_infrastructure` in `tests/architecture/test_boundaries.py` [map #20] — this is what makes the hexagonal claim in ARCHITECTURE.md verifiable
- [X] T034 [P] Write Testcontainers fixtures for MongoDB, Elasticsearch, and Redis in `tests/conftest.py`, with the in-process queue as the default adapter so the suite needs no broker (constitution X.3)

**Checkpoint**: Domain core, ports, and infrastructure ready. User stories can begin.

---

## Phase 3: Architecture Documentation Baseline

**Purpose**: `ARCHITECTURE.md` is the principal deliverable. Its decisions are already made and
recorded in plan.md and research.md, so the ADRs and diagrams are written now, while the reasoning
is fresh, rather than reconstructed at the end (docs/research.md §"Write ADRs while decisions are
fresh").

**This phase does NOT block Phases 4-9.** A documentation author and an implementation author can
work simultaneously. Sections needing observed behaviour are deliberately deferred to Phase 10.

- [X] T035 [P] Write `docs/adr/ADR-001-modular-monolith.md` in Nygard form — alternatives rejected must include microservices, with the reason from constitution V.7
- [X] T036 [P] Write `docs/adr/ADR-002-at-least-once-idempotency.md` — the unique index as a storage-level guarantee, and why a read-then-write check loses the race (research.md R4)
- [X] T037 [P] Write `docs/adr/ADR-003-storage-responsibilities.md` — canonical versus derived, using the ownership table in data-model.md
- [X] T038 [P] Write `docs/adr/ADR-004-tenant-isolation.md` — the required-argument tenant rule, why ambient context was rejected, and JWT named as the production direction (research.md R8)
- [X] T039 [P] Write `docs/adr/ADR-005-projection-reliability-and-outbox.md` — status field plus reconciler now, transactional outbox with change streams as the evolution, and the standalone-MongoDB consequence (research.md R5)
- [X] T040 [P] Write `docs/adr/ADR-006-cache-aside-bounded-staleness.md` — cache-aside over write-through and invalidate-on-write, the 30 s TTL rationale, tenant-keyed entries, Redis-down behaviour, and what changes under higher write volume (research.md R6)
- [X] T041 [P] Write `docs/adr/ADR-007-queue-migration-to-sqs.md` — the adapter comparison table from contracts/queue-message.md, with the 256 KB limit and the long-poll receive loop as the two changes that carry real work
- [X] T042 [P] Write the C4 system context diagram in `docs/diagrams/system-context.md` (Mermaid)
- [X] T043 [P] Write the C4 container diagram in `docs/diagrams/container.md` (Mermaid)
- [X] T044 [P] Write the component diagram in `docs/diagrams/component.md` (Mermaid) showing api → application → ports ← infrastructure
- [X] T045 [P] Write the ingestion success and failure/retry sequence diagram in `docs/diagrams/ingest-sequence.md` (Mermaid)
- [X] T046 Draft `ARCHITECTURE.md` sections 1-7 and 13 in the arc42-lite order fixed by constitution IX, sourcing content per the Documentation Architecture table in plan.md
- [X] T047 [P] Write `docs/event-contract.md` — envelope, required versus optional fields, event-type catalog, validation rules, versioning policy, idempotency definition, which fields are indexed where, which may enter logs, and explicit guidance that a sender should supply its own `event_id` because omitting it makes a retry a second event (spec D2)
- [X] T048 [P] Write `docs/data-model.md` from the store-ownership and index content of `specs/001-distributed-event-platform/data-model.md`
- [X] T049 [P] Write `docs/threat-model.md` — STRIDE-lite covering tenant spoofing, cross-tenant disclosure, malformed payloads, queue flooding, search abuse, and unsafe logging (constitution VIII.7)

**Checkpoint**: Architecture reviewable on its own, before the code that implements it is finished.

---

## Phase 4: User Story 1 - Capture engagement events without slowing the source (Priority: P1) 🎯 MVP

**Goal**: A sender submits an event, gets an immediate receipt, and the event later appears in that
tenant's event list with every field intact and no other tenant's data present.

**Independent Test**: Submit a valid event, observe `202` with `event_id_origin`, poll
`GET /events` until it appears, and confirm a second tenant's key returns none of it.

### Tests for User Story 1

> Write these first and confirm they fail before implementing.

- [X] T050 [P] [US1] Contract test `test_openapi_matches_routes` in `tests/contract/test_openapi.py` [map #21]
- [X] T051 [P] [US1] Integration test `test_ingest_to_query_roundtrip` in `tests/integration/test_ingest_query.py` [map #1]
- [X] T052 [P] [US1] Integration test `test_unknown_and_revoked_credential_identical` in `tests/integration/test_auth.py` [map #18]
- [X] T053 [P] [US1] Integration test `test_keyset_pagination_stable_under_insert` in `tests/integration/test_pagination.py` [map #19]
- [X] T054 [P] [US1] Integration test `test_tenant_a_cannot_read_tenant_b` in `tests/integration/test_isolation.py` covering the listing path [map #9, extended by later stories]

### Implementation for User Story 1

- [X] T055 [P] [US1] Define request schemas in `src/eventplatform/api/schemas/requests.py` — `EventSubmission` with the field rules from contracts/openapi.yaml
- [X] T056 [P] [US1] Define response schemas in `src/eventplatform/api/schemas/responses.py` — `AcceptanceReceipt` carrying `event_id_origin`, and the event page with `next_cursor`
- [X] T057 [US1] Implement the in-process queue adapter in `src/eventplatform/infrastructure/queue/in_memory.py` — bounded capacity, visibility timeout, redelivery, attempt counting (contracts/queue-message.md)
- [X] T058 [US1] Implement the ingest use case in `src/eventplatform/application/ingest_event.py` — validate, assign or accept `event_id`, enqueue, return the receipt; never imply persistence (FR-001, FR-002)
- [X] T059 [US1] Implement `insert` and keyset `find_page` in `src/eventplatform/infrastructure/persistence/mongo/event_repository.py` — cursor over `(occurred_at, event_id)` (research.md R11)
- [X] T060 [US1] Implement the listing use case in `src/eventplatform/application/query_events.py` — tenant-scoped, filtered, paginated, page size capped at 200 (FR-020, FR-021)
- [X] T061 [US1] Implement the worker consume loop in `src/eventplatform/worker/consumer.py` — persist then acknowledge, never the reverse (FR-015)
- [X] T062 [US1] Implement `POST /events` and `GET /events` in `src/eventplatform/api/routes/events.py`
- [X] T063 [US1] Wire the API and worker entrypoints in `src/eventplatform/main.py` and add both to `docker-compose.yml`

**Checkpoint**: MVP. Ingest → queue → worker → MongoDB → tenant-scoped query works end to end.

---

## Phase 5: User Story 2 - Never lose or double-count an event (Priority: P2)

**Goal**: Duplicate deliveries produce exactly one record; failures retry with backoff and end in an
inspectable, replayable dead-letter store.

**Independent Test**: Deliver one event three times and assert a single record plus a
duplicate-suppression count; stop MongoDB, watch bounded retries, then find the event in
`/dead-letters` with its failure reason.

### Tests for User Story 2

- [X] T064 [P] [US2] Integration test `test_triplicate_delivery_single_record` in `tests/integration/test_idempotency.py` [map #5]
- [X] T065 [P] [US2] Integration test `test_conflicting_duplicate_keeps_first` in `tests/integration/test_idempotency.py` [map #6]
- [X] T066 [P] [US2] Integration test `test_retry_then_dead_letter` in `tests/integration/test_retry_dlq.py` [map #7]
- [X] T067 [P] [US2] Integration test `test_dead_letter_replay` in `tests/integration/test_retry_dlq.py` [map #8]
- [X] T068 [P] [US2] Integration test for interruption after persistence but before acknowledgement in `tests/integration/test_idempotency.py` (US2 scenario 4)

### Implementation for User Story 2

- [X] T069 [US2] Return `InsertOutcome` from the repository in `src/eventplatform/infrastructure/persistence/mongo/event_repository.py` — catch `DuplicateKeyError`, compare `content_hash`, distinguish suppression from conflict (research.md R4)
- [X] T070 [P] [US2] Implement the dead-letter store in `src/eventplatform/infrastructure/persistence/mongo/dead_letter_store.py` — full payload retained so replay needs no reconstruction (FR-018)
- [X] T071 [US2] Implement the processing use case in `src/eventplatform/application/process_event.py` — acknowledge on all three outcomes, increment a distinct counter for each
- [X] T072 [US2] Add bounded retry and dead-lettering to `src/eventplatform/worker/consumer.py` using the domain retry policy, so a failing message never blocks unrelated work (FR-019)
- [X] T073 [P] [US2] Implement the replay use case in `src/eventplatform/application/replay_dead_letter.py`
- [X] T074 [US2] Implement `GET /dead-letters` and `POST /dead-letters/{event_id}/replay` in `src/eventplatform/api/routes/dead_letters.py`
- [X] T075 [US2] Emit `duplicate_suppressed`, `duplicate_conflict`, `processing_attempts`, `retries`, and `dead_letter_total` counters in `src/eventplatform/infrastructure/observability/metrics.py` and record them from `src/eventplatform/application/process_event.py` (FR-013, constitution VI.4)

**Checkpoint**: At-least-once delivery is safe. No loss, no double-count, nothing silently dropped.

---

## Phase 6: User Story 3 - Understand engagement over time (Priority: P3)

**Goal**: Counts grouped by event type across hourly, daily, or weekly buckets over a bounded range.

**Independent Test**: Load a known event set across days and types, request daily counts, and assert
the buckets match exactly; switch bucket size and confirm totals are unchanged.

### Tests for User Story 3

- [X] T076 [P] [US3] Integration test `test_ingest_to_stats_roundtrip` in `tests/integration/test_stats.py` [map #2]
- [X] T077 [P] [US3] Integration test for bucket-size invariance and zero-filled empty buckets in `tests/integration/test_stats.py` (US3 scenarios 2 and 4)
- [X] T078 [P] [US3] Integration test for range rejection or explicit clamping in `tests/integration/test_stats.py` (FR-023, US3 scenario 3)
- [X] T079 [P] [US3] Extend `tests/integration/test_isolation.py` to assert the aggregation path is tenant-scoped [map #9]

### Implementation for User Story 3

- [X] T080 [US3] Implement `aggregate_counts` in `src/eventplatform/infrastructure/persistence/mongo/event_repository.py` — `$match` on tenant and range first, then `$group` with `$dateTrunc` (research.md R9)
- [X] T081 [US3] Implement the aggregation use case in `src/eventplatform/application/aggregate_stats.py` — zero-fill empty buckets in the application layer, echo `range_applied` with a `clamped` flag
- [X] T082 [US3] Implement `GET /events/stats` in `src/eventplatform/api/routes/stats.py`

**Checkpoint**: The core analytical question is answerable, tenant-scoped and bounded.

---

## Phase 7: User Story 4 - Find events by what is inside them (Priority: P4)

**Goal**: Free-text search across allowlisted metadata, with a stored event never lost to an
indexing failure.

**Independent Test**: Store events with distinctive metadata, search a term, assert matches for the
caller's tenant only; stop Elasticsearch and confirm ingestion and listing are unaffected.

### Tests for User Story 4

- [X] T083 [P] [US4] Integration test `test_projection_and_metadata_search` in `tests/integration/test_search.py` [map #3]
- [X] T084 [P] [US4] Integration test `test_raw_dsl_is_literal` in `tests/integration/test_search.py` [map #17] — operators must not alter query structure or escape the tenant filter
- [X] T085 [P] [US4] Integration test `test_search_unavailable_degrades` in `tests/integration/test_search.py` [map #11] — ingestion and listing keep working (SC-010)
- [X] T086 [P] [US4] Integration test for reconciler catch-up after an Elasticsearch outage in `tests/integration/test_search.py` (FR-038)
- [X] T087 [P] [US4] Extend `tests/integration/test_isolation.py` to assert the search path is tenant-scoped [map #9]

### Implementation for User Story 4

- [X] T088 [P] [US4] Define the `events-v1` mapping in `src/eventplatform/infrastructure/search/elasticsearch/mappings.py` — `dynamic: strict`, explicit envelope fields, `metadata` as `flattened`, allowlisted `metadata_text` copy (research.md R7)
- [X] T089 [US4] Implement the search index adapter in `src/eventplatform/infrastructure/search/elasticsearch/index.py` — `index`, `search`, `ensure_mapping`
- [X] T090 [US4] Implement the structured query builder in `src/eventplatform/infrastructure/search/elasticsearch/query_builder.py` — the tenant `filter` clause is added by the builder and there is no code path that omits it (FR-025, FR-033)
- [X] T091 [US4] Add the `projection` state field and `update_projection` to the repository, and project inline after acknowledgement in `src/eventplatform/worker/projector.py` (research.md R5)
- [X] T092 [US4] Implement the reconciler in `src/eventplatform/worker/reconciler.py` — scan `projection.status = pending` older than a threshold and retry (FR-038, constitution IV.7)
- [X] T093 [US4] Implement the search use case in `src/eventplatform/application/search_events.py` and `GET /events/search` in `src/eventplatform/api/routes/search.py`, returning the `freshness_note` so no response implies stronger consistency than exists (FR-037)

**Checkpoint**: Metadata search works and survives an Elasticsearch outage without data loss.

---

## Phase 8: User Story 5 - Answer "what is happening right now" instantly (Priority: P5)

**Goal**: A live summary served from cache within a bounded staleness window, degrading to
computation rather than failing when the cache is down.

**Independent Test**: Call the endpoint twice — first `cached: false`, then `cached: true` with an
`age_seconds`; stop Redis and confirm the endpoint still answers with `degraded: true`.

### Tests for User Story 5

- [X] T094 [P] [US5] Integration test `test_cache_miss_then_hit` in `tests/integration/test_realtime.py` [map #4]
- [X] T095 [P] [US5] Integration test `test_cache_unavailable_degrades` in `tests/integration/test_realtime.py` [map #12] (SC-009)
- [X] T096 [P] [US5] Integration test for TTL expiry causing recomputation in `tests/integration/test_realtime.py` (US5 scenario 3)
- [X] T097 [P] [US5] Extend `tests/integration/test_isolation.py` to assert two tenants with identical filters never share a cached entry [map #9, FR-028]

### Implementation for User Story 5

- [X] T098 [P] [US5] Implement key construction in `src/eventplatform/infrastructure/cache/redis/keys.py` using the domain cache-key policy
- [X] T099 [US5] Implement the cache adapter in `src/eventplatform/infrastructure/cache/redis/stats_cache.py` — every method raising `CacheUnavailable` rather than leaking a driver error
- [X] T100 [P] [US5] Implement the single-flight lock in `src/eventplatform/infrastructure/cache/redis/single_flight.py` to prevent the stampede at expiry (research.md R6)
- [X] T101 [US5] Implement the live-summary use case in `src/eventplatform/application/realtime_stats.py` — cache-aside, 30 s TTL, compute-from-MongoDB fallback marked `degraded: true` (FR-030)
- [X] T102 [US5] Implement `GET /events/stats/realtime` in `src/eventplatform/api/routes/stats.py` returning `cached`, `age_seconds`, and `degraded` (FR-029)
- [X] T103 [US5] Emit `cache_hit`, `cache_miss`, `cache_error`, and `cache_fallback` counters from `src/eventplatform/application/realtime_stats.py` via `src/eventplatform/infrastructure/observability/metrics.py` (constitution VI.4)

**Checkpoint**: Low-latency summaries that never fail outright because a derived store is down.

---

## Phase 9: User Story 6 - Operate the platform under stress (Priority: P6)

**Goal**: Honest backpressure, a full set of operational signals, and a durable queue adapter that
proves the port abstraction.

**Independent Test**: Fill the queue to capacity, submit another event, and assert `503` with
`Retry-After` and zero accepted-then-discarded events; inspect `/metrics` for every named signal.

### Tests for User Story 6

- [X] T104 [P] [US6] Integration test `test_backpressure_returns_503` in `tests/integration/test_backpressure.py` [map #13] (SC-013)
- [X] T105 [P] [US6] Integration test asserting every constitution VI.4 signal is present at `/metrics` in `tests/integration/test_observability.py` (SC-014)
- [X] T106 [P] [US6] Integration test asserting `/readyz` fails with the dependency named while `/healthz` still passes, in `tests/integration/test_observability.py` (FR-041)
- [X] T107 [P] [US6] Unit test asserting logs carry correlation ids and contain no raw payload or PII, in `tests/unit/test_logging_redaction.py` (FR-036, FR-039)
- [X] T108 [P] [US6] RabbitMQ adapter suite in `tests/integration/test_rabbitmq_adapter.py`, marked `rabbitmq` and skipped when no broker is reachable — same behavioural assertions as the in-process adapter

### Implementation for User Story 6

- [X] T109 [US6] Enforce queue capacity and a publish timeout in `src/eventplatform/application/ingest_event.py` and the queue adapters — return `503` with `Retry-After`, never accept work intended for discard (FR-005, research.md R12)
- [X] T110 [P] [US6] Implement token-bucket rate limiting in `src/eventplatform/api/middleware/rate_limit.py`, keyed per credential
- [X] T111 [US6] Implement the RabbitMQ adapter in `src/eventplatform/infrastructure/queue/rabbitmq.py` with aio-pika — quorum queues, explicit `prefetch_count`, and the TTL retry-queue ladder with a dead-letter exchange, because RabbitMQ has no native backoff and `nack(requeue=true)` is a hot loop (research.md R3)
- [X] T112 [US6] Add per-dependency readiness reporting to `src/eventplatform/api/routes/health.py`
- [X] T113 [US6] Complete the operational signal set — accepted events, validation failures, queue depth, projection lag and failures, MongoDB write failures — in `src/eventplatform/infrastructure/observability/metrics.py` (constitution VI.4)

**Checkpoint**: All six stories complete. The platform is inspectable and refuses work honestly.

---

## Phase 10: Documentation Completion, Validation & Polish

**Purpose**: Finish the principal deliverable using observed behaviour, and prove every claim.

- [X] T114 Complete `ARCHITECTURE.md` sections 8-12 — runtime scenarios, data architecture, cross-cutting concerns, deployment view, and the 10× scaling analysis from research.md — using behaviour actually observed in Phases 4-9, not predicted
- [X] T115 [P] Write `docs/failure-modes.md` as an FMEA-lite table, with the in-process queue's loss of unprocessed messages on process restart as row one, stated plainly rather than excused (constitution VI.5)
- [X] T116 [P] Write `docs/quality-scenarios.md` mapping SC-001 through SC-016 to source, stimulus, environment, artifact, response, and measure
- [X] T117 [P] Write `docs/observability.md` — each signal, where it is emitted, and what an operator does with it (constitution IX.9)
- [X] T118 Write `README.md` — purpose, one-command quick start, configuration, endpoint summary, test commands, how to inspect each store, known limitations, the explicit statement that `202 Accepted` means queued and not searchable, and the required "AI in My Workflow" section
- [X] T119 [P] Export the served schema to `openapi/openapi.yaml` and add a CI check that fails when it drifts from `specs/001-distributed-event-platform/contracts/openapi.yaml`
- [X] T120 Verify the performance criteria SC-001, SC-006, SC-007, and SC-008 against a seeded dataset and record the measured numbers in `docs/quality-scenarios.md` — publish what was measured, including any criterion that was not met
- [X] T121 Run the full `specs/001-distributed-event-platform/quickstart.md` walkthrough on a clean machine, including every degradation scenario, and correct any instruction that does not work as written
- [X] T122 Audit every guarantee stated in `ARCHITECTURE.md` against the test map, confirming each maps to a named test, a named metric, or a documented limitation, and fix any unbacked claim (SC-016)
- [X] T123 [P] Confirm every ADR in `docs/adr/` still matches the implementation and update any whose consequences changed during build (constitution XI.8)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 Setup**: no dependencies
- **Phase 2 Foundational**: depends on Phase 1 — **blocks Phases 4-9**
- **Phase 3 Documentation Baseline**: depends on Phase 2 only for file layout; **does not block Phases 4-9** and can run alongside them
- **Phases 4-9 User Stories**: all depend on Phase 2; then proceed in priority order or in parallel
- **Phase 10**: depends on the stories being delivered — T114, T120, T121, and T122 need observed behaviour

### User Story Dependencies

- **US1 (P1)**: after Phase 2. No dependency on any other story. This is the MVP.
- **US2 (P2)**: after Phase 2. Hardens the US1 path; testable on its own.
- **US3 (P3)**: after Phase 2. Needs stored events, which US1 produces; the aggregation itself is independent.
- **US4 (P4)**: after Phase 2. Independent — adds the projection and search path.
- **US5 (P5)**: after Phase 2. Independent — reads through the aggregation US3 also uses; if US3 is skipped, implement the read inside this story.
- **US6 (P6)**: after Phase 2. Independent — signals and backpressure apply to whatever exists.

### Within Each Story

Tests written and failing → schemas and adapters → repository and use case → route → wiring.

### Parallel Opportunities

- Phase 1: T003, T004, T005, T006, T009, T010 together
- Phase 2: T012-T017 and T019-T022 together; T030-T034 together
- Phase 3: T035-T045 and T047-T049 all parallel — eleven documents with no shared file
- Every story's test tasks are parallel with each other
- Phase 3 runs in parallel with all of Phases 4-9

---

## Parallel Example: User Story 1

```bash
# Tests first, all parallel:
Task: "Contract test test_openapi_matches_routes in tests/contract/test_openapi.py"
Task: "Integration test test_ingest_to_query_roundtrip in tests/integration/test_ingest_query.py"
Task: "Integration test test_unknown_and_revoked_credential_identical in tests/integration/test_auth.py"
Task: "Integration test test_keyset_pagination_stable_under_insert in tests/integration/test_pagination.py"
Task: "Integration test test_tenant_a_cannot_read_tenant_b in tests/integration/test_isolation.py"

# Then schemas, parallel:
Task: "Define request schemas in src/eventplatform/api/schemas/requests.py"
Task: "Define response schemas in src/eventplatform/api/schemas/responses.py"
```

---

## Implementation Strategy

### MVP first

1. Phase 1 Setup
2. Phase 2 Foundational — blocking
3. Phase 4 User Story 1
4. **Stop and validate**: ingest → queue → worker → MongoDB → tenant-scoped query, proven by tests
   T050-T054
5. Demonstrable at this point

### Incremental delivery

Setup + Foundational → US1 (MVP) → US2 (correctness under redelivery) → US3 (analytics) →
US4 (search) → US5 (cache) → US6 (operability) → Phase 10. The system runs at every checkpoint.

Start Phase 3 as soon as Phase 2 lands. The architecture document is the principal deliverable and
should not be compressed into the final hours.

### If time runs short

Constitution governance is explicit: prefer a smaller, complete vertical slice with stated
limitations over an incomplete simulation of production complexity. Drop whole stories from the
bottom — US6, then US5 — and record each omission in `README.md` under known limitations. Do not
drop Phase 3 or Phase 10; an undocumented implementation scores worse than a documented smaller one
on this assignment's own criteria.

---

## Notes

- `[P]` means a different file with no incomplete dependency
- Every task names its file path; every test task names its test function and its map row
- Verify tests fail before implementing
- Commit per task or per logical group
- Any change to an architectural decision requires updating the matching ADR in the same change
  (constitution XI.8)
