# Multi-Tenant Event Processing Platform

A distributed event processing platform for a multi-tenant nonprofit and association
marketing product. Ingests marketing and website events asynchronously, stores them
immutably per organisation, and serves querying, aggregation, metadata search, and cached
live statistics.

**[`ARCHITECTURE.md`](ARCHITECTURE.md) is the primary document.** It is arc42-lite, with C4
diagrams and seven ADRs.

---

## Quick start

```bash
docker compose up --build
curl -fsS localhost:8000/readyz          # 200 once every dependency is healthy

# Mint a credential — the tenant comes from this, never from the request body
KEY=$(docker compose exec -T api python -m eventplatform.cli issue-key --tenant demo)

# Submit an event. 202 means queued, NOT stored.
curl -sS -X POST localhost:8000/events \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' -d '{
    "event_id":"evt_demo_1","event_type":"donation",
    "occurred_at":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'",
    "contact_id":"contact_42","campaign_id":"spring_appeal","channel":"email",
    "source_url":"https://example.org/give",
    "conversion":{"goal_id":"gift","value":250.00,"currency":"USD"},
    "consent":{"analytics":true,"advertising":false},
    "metadata":{"browser":"Safari","device_type":"mobile"}}'

# Read it back. Keep the range within MAX_AGGREGATE_RANGE_DAYS (92) — a wider range is
# clamped to the most recent 92 days, and the response says so in `range_applied`.
FROM=$(date -u -v-7d +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '7 days ago' +%Y-%m-%dT%H:%M:%SZ)
TO=$(date -u -v+1d   +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '1 day'     +%Y-%m-%dT%H:%M:%SZ)
curl -sS "localhost:8000/events?from=$FROM&to=$TO" -H "Authorization: Bearer $KEY"
```

Interactive docs at <http://localhost:8000/docs>.

## Run the tests — one command

```bash
uv sync --all-extras
uv run pytest                    # 126 tests
```

Unit, architecture, and contract tests need nothing running. Integration tests start real
MongoDB, Elasticsearch, and Redis via Testcontainers (Docker required) and drive the
in-process queue, so no broker is needed.

```bash
uv run pytest tests/unit tests/architecture tests/contract   # fast, no services
uv run pytest tests/integration                              # Testcontainers
uv run ruff check . && uv run mypy && uv run lint-imports    # quality gates
```

## API

| Endpoint | Purpose |
|---|---|
| `POST /events` | Submit an event. **202 = validated and queued, not stored** |
| `GET /events` | List, filtered by type, date range, identity, source URL. Keyset paginated |
| `GET /events/stats` | Counts grouped by type over hour/day/week buckets |
| `GET /events/search` | Free-text search across allowlisted metadata |
| `GET /events/stats/realtime` | Cached live summary, 30 s TTL |
| `GET /dead-letters` | Events that exhausted retries |
| `POST /dead-letters/{id}/replay` | Re-queue a failed event |
| `GET /healthz` `/readyz` `/metrics` | Liveness, readiness, operational signals |

All endpoints except health require `Authorization: Bearer <key>`. Contract:
[`openapi/openapi.yaml`](openapi/openapi.yaml).

## Consistency semantics — read this

- **`202 Accepted` means validated and queued.** It does **not** mean stored, searchable, or
  reflected in any summary.
- `GET /events` reflects canonical storage.
- `GET /events/search` lags behind it. Responses carry a `freshness_note`.
- `GET /events/stats/realtime` may be up to 30 seconds stale. Responses carry `cached`,
  `age_seconds`, and `degraded`.

## Configuration

Environment only; see [`.env.example`](.env.example) for every setting. Notable ones:

| Variable | Default | Meaning |
|---|---|---|
| `QUEUE_BACKEND` | `memory` | `memory` or `rabbitmq` |
| `QUEUE_CAPACITY` | `10000` | Beyond this the API returns 503 rather than dropping events |
| `RETRY_MAX_ATTEMPTS` | `5` | Then dead-letter |
| `REALTIME_CACHE_TTL_SECONDS` | `30` | Live-summary staleness bound |
| `MAX_PAGE_SIZE` | `200` | Hard cap |
| `METADATA_MAX_BYTES` | `16384` | Also depth 3, 50 keys |

## Inspecting state locally

```bash
docker compose exec mongodb mongosh eventplatform --eval 'db.events.countDocuments({})'
docker compose exec mongodb mongosh eventplatform --eval 'db.events.getIndexes()'
curl -s localhost:9200/events-v1/_count
curl -s localhost:9200/events-v1/_mapping | jq '.["events-v1"].mappings.properties.metadata'
docker compose exec redis redis-cli --scan --pattern 'v1:*'
curl -s localhost:8000/metrics | grep -E 'duplicate|dead_letter|queue_depth'
```

## Testing philosophy

Tests exist to prove the architectural claims, not to raise a coverage number. Every
reliability and isolation guarantee in `ARCHITECTURE.md` maps to a named test, a named
metric, or a documented limitation.

The split follows where the risk lives:

- **Unit tests** for pure domain policies — retry schedule, cache keys, time windows,
  metadata limits, content hashing. Fast, no services, and they are where the tricky
  reasoning is.
- **Integration tests against real stores** where the guarantee *is* the store's behaviour.
  Idempotency is tested against a real MongoDB unique index, because a fake would only
  prove the fake works — and `test_concurrent_duplicates_cannot_both_insert` runs eight
  concurrent inserts to exercise the race a read-then-write check would lose.
- **An architecture test** that runs the real import contract, so "the domain imports no
  infrastructure" is a build failure rather than a paragraph.
- **A contract test** comparing the served OpenAPI against the committed one, because a
  contract nobody checks becomes fiction.

**With more time**: a load test (the p95 targets are currently design targets, not
results — `docs/quality-scenarios.md` says so explicitly), property-based tests over the
event envelope, and a chaos test that restarts the worker mid-batch rather than simulating
the crash point.

## Known limitations

Stated rather than implied by omission. Full detail in
[`docs/failure-modes.md`](docs/failure-modes.md).

1. **The in-process queue loses accepted-but-unprocessed events on restart.** The most
   important limitation here. It is why the RabbitMQ adapter and
   [ADR-007](docs/adr/ADR-007-queue-migration-to-sqs.md) exist.
2. **No retention, archival, or erasure workflow.** Significant for constituent data.
3. **Rate limiting is per-replica**, so the effective limit scales with replica count.
4. **No authorization beyond the tenant boundary.** Any credential does everything its
   tenant can.
5. **Performance targets are not load-tested.** Correctness is verified at every path;
   latency at 10M events is not.
6. **Single-node stores in Compose.** No replication.
7. **Worker metrics are not exposed over HTTP.** The worker's counters increment in its own
   process; `GET /metrics` on the API shows API-side counters only. Worker activity is in
   `docker compose logs worker`.

## AI in My Workflow

**Tools.** Claude Code (Opus), driving a spec-first workflow: a written constitution, then
specification, plan, task breakdown, and implementation, each as a reviewable artifact
under `.specify/` and `specs/`.

**Where it helped most — pressure-testing decisions, not generating code.** The valuable
output was the *rejected alternatives* in each ADR. Working through why cache-aside beats
write-through under a write-heavy stream, or why a read-then-write idempotency check loses
a race a unique index cannot, produced better reasoning than I would have written
unprompted — and those arguments are what the ADRs actually contain.

**Where I pushed back.**

- **Motor.** The obvious async MongoDB driver, and every example uses it. Checking PyPI
  showed it passed its deprecation date on 2026-05-14. Switched to PyMongo's native
  `AsyncMongoClient`. Verify versions; do not trust training data for anything dated.
- **A replica set for MongoDB.** Earlier notes in this repo assumed one was needed for
  change streams. Once projection state moved onto the event document, nothing needed
  transactions or change streams — so the replica set came out, removing an init container
  and a startup race. The pattern was carried in from a different design.
- **Latency claims.** The draft architecture stated p95 figures as though measured. They
  were not. They are now labelled design targets, with a "what was not measured" section in
  `docs/quality-scenarios.md`.
- **Test-first framing.** Several early tests asserted implementation shape rather than
  behaviour. Rewritten around the guarantee — "eight concurrent inserts produce one record"
  rather than "insert calls the repository once".

**What the tooling caught that review would not have.** Every one of these was a real
defect, and none was found by reading code:

- An `asyncio.timeout` block with no `await` inside it — the publish timeout was inert.
- A mutable set as a class attribute, shared across every queue instance.
- `aggregate()` returning a coroutine in PyMongo's async API, so `.to_list()` was being
  called on the coroutine.
- `first_failed_at` in both `$set` and `$setOnInsert`, which MongoDB rejects.
- The worker importing its composition root from the API package — caught by the import
  contract, and the reason `composition.py` exists.
- Test pollution: the API fixture cleared MongoDB but not Elasticsearch or Redis, an
  order-dependent failure that passed in isolation.

**How it shaped the approach.** Writing the constitution and specification before any code
meant the ADRs were written while the reasoning was fresh rather than reconstructed
afterward, and the tests were derived from a 21-row map of guarantees rather than from the
code. The compounding effect was in the boundaries: because the layering was decided and
then *enforced by a failing build*, the architecture document describes something checkable
rather than something aspirational.

## Repository layout

```
ARCHITECTURE.md              Primary document (arc42-lite)
docs/
  adr/                       Seven Nygard-form ADRs
  diagrams/                  C4 context, container, component, sequences (Mermaid)
  event-contract.md          Envelope, validation, versioning, PII
  data-model.md              Store ownership, indexes, mappings, keys
  failure-modes.md           FMEA-lite + operator runbook
  quality-scenarios.md       Measurable scenarios; what was not measured
  observability.md           Signals, logs, health, alerting
  threat-model.md            STRIDE-lite
openapi/openapi.yaml         Committed API contract
specs/001-.../               Spec, plan, research, tasks, contracts
src/eventplatform/
  domain/                    Imports nothing
  application/               Use cases, depend on ports only
  ports/                     Protocols
  infrastructure/            Adapters
  api/  worker/              Entry points
  composition.py             The only module that knows which adapter is mounted
tests/                       unit · integration · contract · architecture
```
