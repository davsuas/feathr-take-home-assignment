<!--
SYNC IMPACT REPORT
==================
Version change: 1.0.0 → 1.1.0 (2026-08-31)
Bump rationale: MINOR. Principle IX is materially expanded — the required documentation set now
names the arc42-lite structure for ARCHITECTURE.md, four additional documents, a diagrams
directory, and a published API contract. No principle was removed, weakened, or redefined, and no
existing requirement was invalidated, so this is additive guidance rather than a breaking change.

Modified principles:
  IX. Documentation Is a First-Class Deliverable — expanded (see below)
  No other principle changed.

Added to Principle IX:
  - ARCHITECTURE.md MUST follow the arc42-lite section order, enumerated explicitly
  - docs/data-model.md, docs/observability.md, docs/threat-model.md added to the required set
  - docs/diagrams/ named as the home for the four required diagrams
  - Diagrams MUST be Mermaid; ADRs MUST use the Nygard form
  - A published API contract (openapi/) MUST be committed, not only generated at runtime

Source of the expansion: docs/research.md (architecture-documentation research already in the
repository) — arc42-lite, C4 via Mermaid, Nygard ADRs, and the recommended repository layout.

Templates read at runtime and NOT modified: plan/spec/tasks/checklist templates.

--- previous entry -------------------------------------------------------------
Version change: TEMPLATE (unversioned placeholders) → 1.0.0
Bump rationale: MAJOR-equivalent initial ratification. The file previously contained only
unfilled template placeholders; this is the first concrete governance definition, so it
establishes the 1.0.0 baseline rather than incrementing an existing version.

Modified principles (placeholder → concrete):
  [PRINCIPLE_1_NAME] → I. Product Context and Tenant Isolation Are Non-Negotiable
  [PRINCIPLE_2_NAME] → II. Events Are Immutable, Versioned Facts
  [PRINCIPLE_3_NAME] → III. At-Least-Once Delivery Requires Idempotent Processing
  [PRINCIPLE_4_NAME] → IV. MongoDB Is Canonical; Search and Cache Are Derived
  [PRINCIPLE_5_NAME] → V. Clear Boundaries Through a Modular Monolith

Added principles (beyond the 5 template slots, per user input):
  VI. Explicit Reliability, Degradation, and Operability
  VII. Query Performance Is Designed, Measured, and Bounded
  VIII. Secure-by-Default Event Handling
  IX. Documentation Is a First-Class Deliverable
  X. Tests Prove Architectural Claims
  XI. Delivery Quality and Reproducibility

Added sections:
  Product Context and Scope (preamble; from user-supplied Purpose)
  Required Initial ADRs (fills [SECTION_2_NAME]/[SECTION_2_CONTENT])
  Development Workflow and Quality Gates (fills [SECTION_3_NAME]/[SECTION_3_CONTENT])

Removed sections: none (all template slots filled; instructional HTML comments removed)

Deferred TODOs: none. RATIFICATION_DATE set to 2026-08-31, the date of first adoption;
no earlier adoption record exists in git history (single "Initial commit", 2026-08-27).

Templates read at runtime and NOT modified by this command:
  .specify/templates/plan-template.md, spec-template.md, tasks-template.md,
  checklist-template.md — they consume this constitution and require no edits here.
-->

# Multi-Tenant Marketing Event Platform Constitution

## Product Context and Scope

This repository implements a production-minded backend assessment for a Feathr-aligned
distributed event-processing platform.

The system ingests high-volume marketing and website events asynchronously; stores canonical
events; supports tenant-scoped querying, aggregation, metadata search, and cached real-time
statistics; and demonstrates how the design can evolve toward a durable cloud queue such as
AWS SQS.

The intended domain is a multi-tenant nonprofit and association marketing platform. Events
represent visitor engagement and measurable outcomes such as page views, ad and email
interactions, registrations, memberships, donations, renewals, and other conversions.

This is a backend-only system. A frontend is explicitly out of scope.

## Core Principles

### I. Product Context and Tenant Isolation Are Non-Negotiable

All data, processing, querying, indexes, cache keys, logs, and authorization assumptions
MUST be tenant-aware.

1. Every persisted event MUST have a `tenant_id`.
2. Every externally visible query MUST be scoped by the authenticated or assumed tenant context.
3. A request MUST NOT be able to retrieve, search, aggregate, infer, or receive cached data
   from another tenant.
4. MongoDB indexes and unique constraints MUST begin with or include `tenant_id` whenever the
   query or uniqueness definition is tenant-specific.
5. Redis cache keys MUST include `tenant_id` and every filter dimension that affects the response.
6. Elasticsearch documents and queries MUST include tenant filtering.
7. Tests MUST prove tenant isolation for the primary query, search, statistics, and cache paths.
8. Tenant identity SHOULD come from a trusted authentication/authorization boundary in
   production; the take-home MAY use a documented development/test tenant-resolution mechanism.

Rationale: Feathr-like customers include distinct nonprofits, associations, chapters, event
organizations, and fundraising organizations. Cross-tenant data access is a critical
correctness and security failure.

### II. Events Are Immutable, Versioned Facts

The platform treats an event as an immutable record of something that occurred, not as mutable
application state.

1. Each event MUST include: `event_id`, `tenant_id`, `event_type`, `occurred_at`, `received_at`,
   source or provenance information, and schema version.
2. Event timestamps MUST use UTC and ISO 8601 serialization at API boundaries.
3. Events MUST support anonymous and known identities independently, using fields such as
   `anonymous_id`, `contact_id`, and `session_id` where relevant.
4. Events SHOULD support marketing attribution dimensions such as campaign, channel, source URL,
   conversion goal, and monetary value.
5. Flexible metadata MUST be validated for size, shape, and supported types.
6. Raw sensitive data, especially direct PII, MUST NOT be indiscriminately copied into logs,
   cache keys, search indexes, or error messages.
7. Breaking changes to event shape MUST use a new schema version or an explicit migration strategy.
8. Event contracts MUST be documented in `docs/event-contract.md`.

Rationale: Event data is the foundation for attribution, audience intelligence, reporting,
debugging, and downstream projections. Immutability and versioning preserve auditability and
safe evolution.

### III. At-Least-Once Delivery Requires Idempotent Processing

The platform MUST be designed for at-least-once delivery, not exactly-once delivery.

1. The ingestion API MUST return `202 Accepted` only after validation and successful queue
   acceptance.
2. A `202 Accepted` response MUST NOT be interpreted as proof that the event is already persisted
   in MongoDB, indexed in Elasticsearch, or reflected in Redis statistics.
3. The worker MUST safely tolerate duplicate deliveries.
4. Canonical event persistence MUST be idempotent using a stable idempotency key, normally
   `(tenant_id, event_id)`.
5. MongoDB MUST enforce idempotency through a unique constraint or equivalent atomic persistence
   behavior.
6. The worker MUST acknowledge a queue message only after required canonical persistence succeeds.
7. Processing failures MUST use bounded retries with exponential backoff and jitter.
8. Events that exhaust retry attempts MUST be preserved in a dead-letter queue or dead-letter
   store with failure metadata.
9. Duplicate detection MUST be observable and tested.
10. The simulated queue MUST document its limitations relative to a production SQS-style queue,
    including durability, visibility timeout, redelivery, ordering, and process-restart behavior.

Rationale: Distributed systems encounter retries, worker crashes, timeouts, and duplicate
delivery. Correctness comes from idempotent consumers and explicit semantics, not from assuming
exactly-once processing.

### IV. MongoDB Is Canonical; Search and Cache Are Derived

MongoDB is the canonical durable event store. Elasticsearch and Redis are derived, disposable,
eventually consistent read models.

1. MongoDB MUST own canonical immutable event records and idempotency enforcement.
2. Elasticsearch MUST be used for full-text and search-oriented queries, not as the authoritative
   source of event truth.
3. Redis MUST be used for low-latency cached or materialized summaries, not as the only durable
   source of business data.
4. Failure to index an event in Elasticsearch MUST NOT silently discard a successfully persisted
   MongoDB event.
5. Failure of Redis MUST NOT cause event loss; real-time statistics MUST degrade safely through a
   documented fallback or explicit degraded response.
6. The system MUST document eventual-consistency behavior: MongoDB events may be visible before
   Elasticsearch indexing completes; Redis real-time statistics may be stale within the configured
   freshness window; query APIs MUST NOT imply stronger consistency than they provide.
7. Projection failures MUST be observable and retryable.
8. The architecture SHOULD describe an outbox or equivalent reliable projection strategy for
   production, even if the take-home uses a simplified implementation.

Rationale: Separating the canonical write model from search and cache projections makes failures
recoverable and allows each store to be used for its appropriate workload.

### V. Clear Boundaries Through a Modular Monolith

The project MUST use a modular-monolith architecture with clean or hexagonal boundaries.

1. Domain and application logic MUST NOT depend directly on FastAPI, MongoDB, Redis,
   Elasticsearch, AWS SDKs, or concrete queue implementations.
2. HTTP handlers MUST remain thin: validate/translate requests, invoke application use cases, and
   map outcomes to HTTP responses.
3. Application services or use cases MUST coordinate ingestion, processing, query, search,
   statistics, and cache policies.
4. Infrastructure dependencies MUST be implemented through ports/interfaces and adapters.
5. The codebase MUST keep these concerns distinct: API transport; domain event definitions and
   policies; ingestion; queueing; worker processing; persistence; search projection/querying;
   caching; observability; configuration.
6. Replacing the in-memory queue with AWS SQS SHOULD require changing an adapter, configuration,
   and deployment details — not rewriting domain or application behavior.
7. Microservices MUST NOT be introduced unless a documented constraint proves they are necessary.

Rationale: The take-home needs production-quality boundaries without the delivery and operational
overhead of premature microservices.

### VI. Explicit Reliability, Degradation, and Operability

The platform MUST fail explicitly, preserve recoverability, and provide enough telemetry to
investigate behavior.

1. The system MUST use structured logs with correlation identifiers such as request ID, event ID,
   tenant ID, and worker/message ID where applicable.
2. Logs MUST avoid unsafe payload dumping and direct PII exposure.
3. The application MUST expose health/readiness behavior appropriate for local Docker Compose use.
4. Important operational signals MUST be measurable or logged: accepted event count; validation
   failures; queue depth; processing attempts; retries; duplicate suppressions; dead-letter count;
   MongoDB write failures; Elasticsearch projection lag/failures; Redis cache
   hit/miss/error/fallback behavior.
5. Failure modes for MongoDB, Elasticsearch, Redis, queue capacity, worker crashes, and poison
   messages MUST be documented in `docs/failure-modes.md`.
6. Backpressure MUST be explicit. If the queue cannot safely accept work, the API MUST reject
   requests with an appropriate response rather than silently losing events.
7. Production recommendations MUST distinguish what the local assessment simulates from what a
   durable production deployment requires.

Rationale: An event platform is operational software. Correct code without inspectable failure
behavior is insufficient.

### VII. Query Performance Is Designed, Measured, and Bounded

Indexes, pagination, aggregation, caching, and search mappings MUST derive from documented access
patterns.

1. MongoDB indexes MUST support the documented filters: tenant; event type; time range;
   user/contact/anonymous identity where supported; source URL; campaign or conversion dimensions
   where implemented.
2. MongoDB aggregation queries MUST be tenant-scoped and use bounded date ranges.
3. Event-list endpoints MUST paginate and impose safe page-size limits.
4. Elasticsearch mappings MUST be explicit for core fields.
5. Exact-match/filter dimensions MUST use keyword-like mapping behavior.
6. Full-text analysis MUST be limited to selected, meaningful metadata fields.
7. Dynamic mapping of arbitrary customer metadata SHOULD be restricted or disabled to avoid
   mapping explosion.
8. Redis TTL and invalidation/versioning policy MUST be documented, including the acceptable
   staleness window.
9. Deliberately omitted indexes MUST be documented with their write, storage, or selectivity
   trade-offs.
10. The architecture MUST explain what becomes a bottleneck at 10x volume and how it would evolve.

Rationale: Event platforms are write-heavy and query-sensitive. Indexing everything or caching
without a coherence strategy is not a valid performance design.

### VIII. Secure-by-Default Event Handling

The system MUST treat externally supplied events and metadata as untrusted input.

1. API input MUST be schema-validated.
2. Payload size, metadata depth, and metadata complexity MUST have safe limits.
3. Search endpoints MUST use structured query construction and MUST NOT accept raw Elasticsearch
   query DSL from clients.
4. Date range, pagination, aggregation bucket, and search parameters MUST be validated and bounded.
5. Configuration and secrets MUST come from environment/configuration mechanisms, never hardcoded
   source files.
6. Repository commits MUST NOT contain real credentials, personal data, or production endpoints.
7. A lightweight threat model MUST address: tenant spoofing; cross-tenant disclosure; malformed
   event payloads; queue flooding/backpressure; search abuse; unsafe logging.
8. Security-related assumptions and non-goals MUST be documented.

Rationale: Marketing and fundraising telemetry can contain sensitive constituent and behavioral
information; isolation and input safety are core architectural concerns.

### IX. Documentation Is a First-Class Deliverable

Architecture documentation MUST evolve with the code and be sufficient for an engineer to
understand, run, test, and challenge the system. The repository MUST include:

1. `README.md` — project purpose; quick start; configuration; API overview; test commands; known
   limitations; AI-in-workflow disclosure.
2. `ARCHITECTURE.md` — product context and assumptions; system context; container/component
   responsibilities; data flow; storage rationale; consistency semantics; failure modes; scaling
   analysis; deployment direction; links to ADRs.
3. `docs/event-contract.md` — event envelope; validation rules; event types; identity and
   attribution fields; schema-versioning policy; PII and consent assumptions.
4. `docs/adr/` — one ADR per architecturally significant decision.
5. `docs/failure-modes.md` — dependency failure behavior; data-loss implications; recovery
   approach; alerts/metrics.
6. `docs/quality-scenarios.md` — measurable performance, reliability, isolation, and consistency
   scenarios.
7. `docs/diagrams/` — C4 system context diagram; C4 container diagram; component/module diagram;
   ingestion and failure/retry sequence diagram.
8. `docs/data-model.md` — per-store ownership, what each store does not own, and its consistency
   class.
9. `docs/observability.md` — the operational signals of Principle VI, where each is emitted, and
   what an operator does with it.
10. `docs/threat-model.md` — the STRIDE-lite model required by Principle VIII.7.
11. `openapi/` — the API contract, committed to the repository rather than only generated at
    runtime, so it is reviewable in a diff.

`ARCHITECTURE.md` is the primary narrative and MUST follow the arc42-lite section order:
(1) Executive Summary; (2) Business Context and Assumptions; (3) Goals and Quality Attributes;
(4) Constraints; (5) System Context; (6) Solution Strategy; (7) Container and Component Views;
(8) Runtime Scenarios; (9) Data Architecture; (10) Cross-Cutting Concerns; (11) Deployment View;
(12) Risks, Scaling, and Future Evolution; (13) ADR Index.

Diagrams MUST be authored as Mermaid in Markdown so they render in the repository host and diff as
text. ADRs MUST use the Nygard form: Title, Status, Context, Decision, Consequences. An ADR MUST
record the alternatives rejected and what the decision makes harder, not only what it makes easier.

Documentation MUST be concise, accurate, and aligned with the implementation. Diagrams-as-code are
preferred so they can be reviewed and maintained with the repository.

Rationale: The assessment explicitly evaluates architecture communication and trade-off reasoning.
Documentation is part of the product, not an afterthought.

### X. Tests Prove Architectural Claims

Tests MUST verify business behavior, integration boundaries, and failure semantics — not merely
implementation details.

1. Unit tests MUST cover: event validation; idempotency decisions; retry/backoff behavior;
   cache-key construction; tenant scoping; query/filter validation; error mapping.
2. Integration tests MUST cover at least: ingest -> queue -> worker -> MongoDB -> event query;
   ingest -> queue -> worker -> MongoDB -> statistics query; Elasticsearch projection and metadata
   search; Redis cache miss -> populate -> cache hit; duplicate message delivery without duplicate
   canonical persistence; retry and dead-letter behavior; tenant A cannot access tenant B data.
3. Tests MUST NOT require external production services.
4. Integration tests SHOULD use Docker/Testcontainers or a documented local Compose environment.
5. The test suite MUST be runnable through a single documented command.
6. Every documented reliability or security guarantee SHOULD correspond to an automated test,
   metric, or clearly identified limitation.

Rationale: Architectural decisions are credible when behavior is executable and verifiable.

### XI. Delivery Quality and Reproducibility

The repository MUST be easy for evaluators and future engineers to run reliably.

1. Dependencies MUST be pinned with a lockfile.
2. Python, FastAPI, database drivers, Elasticsearch client, Redis client, and test tooling MUST use
   current supported versions verified at implementation time.
3. The project SHOULD target Python 3.13 unless a documented compatibility constraint requires
   another version.
4. The project MUST provide Docker Compose for the application, MongoDB, Elasticsearch, and Redis.
5. Service health checks and startup dependencies MUST be documented.
6. Code quality MUST include formatting, linting, and type checking appropriate to Python.
7. CI SHOULD run: linting; type checks; unit tests; integration tests where service availability
   permits.
8. Changes that alter an architectural decision MUST update the related ADR and architecture
   documentation.

Rationale: A principal-level backend submission should be reproducible, inspectable, and
maintainable by a team.

## Required Initial ADRs

The following ADRs MUST be created before or alongside implementation, under `docs/adr/`:

1. ADR-001: Use a modular monolith with hexagonal boundaries.
2. ADR-002: Use at-least-once processing and consumer-side idempotency.
3. ADR-003: MongoDB is canonical; Elasticsearch and Redis are derived projections.
4. ADR-004: Enforce tenant isolation across storage, querying, and caching.
5. ADR-005: Define Elasticsearch projection reliability and outbox evolution.
6. ADR-006: Use cache-aside Redis statistics with bounded staleness.
7. ADR-007: Define the migration path from the in-memory queue to AWS SQS.

## Development Workflow and Quality Gates

1. Every implementation specification, plan, and pull request MUST be checked against this
   constitution before approval.
2. A change is not complete until its code, tests, and affected documentation (README,
   ARCHITECTURE, ADRs, event contract, failure modes) land together.
3. A pull request that touches ingestion, persistence, projection, caching, or tenant scoping MUST
   state which principle it exercises and which test proves it.
4. The full test suite MUST pass through the single documented command before merge.
5. Known gaps MUST be recorded as explicit documented limitations rather than left implicit.
6. When delivery-time trade-offs are necessary, prefer a smaller, complete vertical slice with
   explicit limitations over an incomplete simulation of production complexity.

## Governance

This constitution overrides convenience-driven implementation choices.

Any change that weakens tenant isolation, event immutability, idempotency, canonical-data
ownership, failure recoverability, or testability requires:

1. A documented rationale.
2. An ADR describing alternatives and consequences.
3. Updated tests.
4. Updated architecture documentation.
5. Explicit acknowledgment of the resulting risk or limitation.

Amendments to this constitution MUST be proposed in a pull request that states the affected
principles and the resulting version bump. Versioning follows semantic versioning: MAJOR for
backward-incompatible governance or principle removals/redefinitions; MINOR for a new principle
or materially expanded guidance; PATCH for clarifications and non-semantic refinements.

All pull requests and implementation specifications MUST be checked against this constitution
before approval.

**Version**: 1.1.0 | **Ratified**: 2026-08-31 | **Last Amended**: 2026-08-31
