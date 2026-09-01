# Quickstart and Validation Guide

**Plan**: [plan.md](./plan.md) | **Contracts**: [contracts/](./contracts/)

This is the validation guide: how to run the system and how each architectural claim is proven.
Implementation belongs in `tasks.md`; this file stays a run-and-verify reference.

## Prerequisites

Docker with Compose, and `uv`. Nothing else — no local Python, MongoDB, Elasticsearch, or Redis
install is required.

## Run everything

```bash
docker compose up --build          # api, worker, mongodb, elasticsearch, redis, rabbitmq
curl -fsS localhost:8000/readyz    # 200 once every dependency reports healthy
```

Compose gates startup on `condition: service_healthy` rather than bare `depends_on`. Bare
`depends_on` waits only for the container to *start*, so the API would race Elasticsearch's boot and
crash-loop. Health checks: `mongosh --eval "db.adminCommand('ping').ok"`, Elasticsearch
`_cluster/health` accepting green or yellow (single node is yellow by design), `redis-cli ping`,
`rabbitmq-diagnostics -q check_running`.

## Run the whole test suite — one command

```bash
uv run pytest
```

Constitution X.5 requires exactly one documented command. Unit and architecture tests need no
services. Integration tests start real MongoDB, Elasticsearch, and Redis through Testcontainers and
drive the **in-process** queue adapter, so no broker is needed and the suite is self-contained
(X.3). The RabbitMQ adapter has its own marked suite, skipped unless a broker is reachable.

```bash
uv run pytest tests/unit           # fast, no services
uv run pytest tests/integration    # Testcontainers
uv run pytest -m rabbitmq          # requires a broker
uv run ruff check . && uv run mypy src && uv run lint-imports
```

## Prove it works end to end

```bash
KEY=$(docker compose exec -T api python -m eventplatform.cli issue-key --tenant demo)

# 1. Submit. Note 202 and event_id_origin: sender.
curl -sS -X POST localhost:8000/events -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' -d '{
    "event_id":"evt_demo_1","event_type":"donation",
    "occurred_at":"2026-08-31T20:30:00Z","contact_id":"contact_42",
    "campaign_id":"spring_appeal","channel":"email",
    "source_url":"https://example.org/give",
    "conversion":{"goal_id":"gift","value":250.00,"currency":"USD"},
    "consent":{"analytics":true,"advertising":false},
    "metadata":{"browser":"Safari","device_type":"mobile"}}'

# 2. Same submission again. Still 202 to the sender; exactly one record exists.
#    duplicate_suppressed increments — check /metrics.

# 3. Read it back.
# Keep ranges within 92 days; wider is clamped and reported in `range_applied`.
curl -sS "localhost:8000/events?from=2026-08-01T00:00:00Z&to=2026-09-01T00:00:00Z" \
  -H "Authorization: Bearer $KEY"

# 4. Aggregate.
curl -sS "localhost:8000/events/stats?from=2026-08-01T00:00:00Z&to=2026-09-01T00:00:00Z&bucket=day" \
  -H "Authorization: Bearer $KEY"

# 5. Search metadata. May lag a moment behind step 3 — that is the documented behaviour.
curl -sS "localhost:8000/events/search?q=Safari" -H "Authorization: Bearer $KEY"

# 6. Live summary. First call cached:false, second cached:true with age_seconds.
curl -sS localhost:8000/events/stats/realtime -H "Authorization: Bearer $KEY"
curl -sS localhost:8000/events/stats/realtime -H "Authorization: Bearer $KEY"
```

## Prove it degrades correctly

```bash
docker compose stop elasticsearch
#   -> POST /events still 202, event still stored and listable (FR-038)
#   -> GET /events/search returns 503, explicitly
#   -> projection.status stays pending
docker compose start elasticsearch
#   -> the reconciler indexes the backlog; search catches up with no data loss

docker compose stop redis
#   -> GET /events/stats/realtime still 200, with degraded:true (FR-030)
docker compose start redis

docker compose stop mongodb
#   -> worker retries with backoff, does not acknowledge
#   -> after 5 attempts the event lands in /dead-letters with its failure reason
docker compose start mongodb
curl -sS -X POST localhost:8000/dead-letters/evt_demo_1/replay -H "Authorization: Bearer $KEY"
```

## Test-to-criterion map

Constitution X.6 and SC-016 require every guarantee to be backed by a named test, a named metric, or
a documented limitation. This table is that mapping and is the checklist for `/speckit-tasks`.

| # | Test | Proves | Kind |
|---|---|---|---|
| 1 | `test_ingest_to_query_roundtrip` | Ingest → queue → worker → Mongo → list | integration |
| 2 | `test_ingest_to_stats_roundtrip` | Ingest → queue → worker → Mongo → stats | integration |
| 3 | `test_projection_and_metadata_search` | Elasticsearch projection and search (SC-011) | integration |
| 4 | `test_cache_miss_then_hit` | Miss → populate → hit, `age_seconds` sane (SC-008) | integration |
| 5 | `test_triplicate_delivery_single_record` | Duplicate delivery, one record, counter moves (SC-003) | integration |
| 6 | `test_conflicting_duplicate_keeps_first` | FR-014 conflict path | integration |
| 7 | `test_retry_then_dead_letter` | Bounded retry, then DLQ with metadata (SC-004) | integration |
| 8 | `test_dead_letter_replay` | FR-018 replay without reconstruction | integration |
| 9 | `test_tenant_a_cannot_read_tenant_b_events` | Isolation across list, stats, search, cache (SC-005) | integration |
| 10 | `TestCacheKeyNeverSharedAcrossTenants` | FR-028 key construction | unit |
| 11 | `test_search_unavailable_degrades` | SC-010 — ingestion and listing unaffected | integration |
| 12 | `test_cache_unavailable_degrades` | SC-009 — answers with `degraded: true` | integration |
| 13 | `test_backpressure_returns_503` | SC-013 — no receipt is issued then discarded | integration |
| 14 | `TestMetadataLimitsRejected` | SC-012 — size, depth, key count, types | unit |
| 15 | `TestOccurredAtWindow` | R15 — future and stale rejection | unit |
| 16 | `TestRetryScheduleBackoffJitter` | Schedule is a pure, bounded function | unit |
| 17 | `test_raw_dsl_is_literal` | FR-025 — operators cannot alter query structure | integration |
| 18 | `test_unknown_and_revoked_credential_identical` | FR-034 — no existence probing | integration |
| 19 | `test_inserting_newer_events_mid_walk_causes_no_skip_or_repeat` | FR-021 — stable order, no skips or repeats | integration |
| 20 | `test_domain_imports_no_infrastructure` | Principle V — hexagonal boundary | architecture |
| 21 | `tests/contract/test_openapi.py` | Committed contract does not drift | contract |

Two guarantees have **no test and are documented limitations instead**, recorded in
`docs/failure-modes.md`: in-process queue durability across process restart, and the absence of a
retention or erasure workflow. Naming them here is deliberate — an unbacked claim would violate
SC-016, and a quiet omission would be worse than the limitation itself.
