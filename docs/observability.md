# Observability

Every signal, where it comes from, and what an operator does with it.

## Signals

| Signal | Emitted from | What it tells you |
|---|---|---|
| `events_accepted_total{tenant_id,event_type}` | `ingest_event` | Ingestion volume. Flat when it should not be means senders are failing or being rejected upstream |
| `events_rejected_validation_total{field}` | API error handler | A sender is broken. The `field` label says how |
| `ingest_rejected_backpressure_total` | `ingest_event` | The queue is full and the API is refusing work. Scale workers |
| `ingest_rejected_rate_limit_total` | rate-limit middleware | A sender is flooding, or the limit is too tight |
| `processing_attempts_total` | `process_event` | Worker throughput |
| `processing_retries_total` | worker | A dependency is unhealthy. Sustained non-zero precedes dead letters |
| `duplicate_suppressed_total` | `process_event` | Idempotency working. Non-zero is **normal** under at-least-once |
| `duplicate_conflict_total` | `process_event` | Two different events claimed one id. A sender bug |
| `dead_letter_total` | worker | Events that exhausted retries. **Should alert** |
| `mongo_write_failures_total` | worker | Canonical store trouble. **Should alert** |
| `projection_failures_total` | `process_event` | Search index trouble |
| `projection_reconciled_total` | reconciler | Recovery working. Compare against failures |
| `projection_lag_seconds` | reconciler | Age of the oldest pending projection. Seconds normal, minutes not |
| `cache_hit_total` / `cache_miss_total` | `realtime_stats` | Cache effectiveness. Hit ratio below ~90% under steady polling means the TTL is too short |
| `cache_error_total` / `cache_fallback_total` | `realtime_stats` | Redis unhealthy; responses are degraded but correct |
| `search_unavailable_total` | search route | Elasticsearch unreachable |
| `queue_depth` | worker loop | Backlog. Rising means consumers cannot keep up |
| `http_request_duration_seconds{route}` | correlation middleware | Latency per route, for SC-001/006/007/008 |
| `processing_duration_seconds` | worker | Per-message cost |

Exposed at `GET /metrics` in Prometheus format.

### A gap worth naming

**`GET /metrics` on the API exposes API-process counters only.** The worker is a separate
process with its own registry, so `duplicate_suppressed_total`, `dead_letter_total`,
`processing_retries_total`, and the projection counters read as zero there — they are
incrementing in the worker, not the API. Confirmed by running the Compose stack: the worker
logged `duplicate_suppressed` while the API reported `duplicate_suppressed_total 0.0`.

In production each process is scraped separately and Prometheus aggregates them, which is
the normal arrangement. For local inspection today, worker activity is visible in the
worker's structured logs (`docker compose logs worker`) rather than at `/metrics`. Giving
the worker its own metrics endpoint is small and worth doing; it is listed as a limitation
rather than left as a surprise.

**Label cardinality is bounded deliberately.** `tenant_id` appears only on ingest counters,
where the tenant count is small and known. Putting it on per-event processing counters
would multiply every series by the customer count and eventually take out Prometheus.

## Structured logs

JSON via structlog. Every line inside a request or message scope carries `request_id`,
`tenant_id`, `event_id`, and `worker_id` as available, so one event can be traced from the
HTTP call through the worker to a dead letter.

`X-Request-Id` is accepted from the caller and echoed on the response, so a sender's trace
id survives into our logs.

### Redaction is a control, not tidiness

A fixed key set is dropped before rendering: `payload`, `metadata`, `authorization`,
`api_key`, `credential`, `token`, `password`, `email`, `ip`, `ip_address`, `user_agent`.

Dropped wholesale rather than masked — a masked value still leaks length and shape. Event
metadata is customer-supplied and can carry constituent data, and a log line is a copy of
that data in a system with different retention and access rules than the event store.
`tests/unit/test_logging_redaction.py` asserts this.

## Health

`GET /healthz` — liveness. Is the process alive.

`GET /readyz` — readiness, with a per-dependency breakdown. Returns 503 only when
**MongoDB** is unreachable, because it alone is canonical: Elasticsearch or Redis being
down is degraded, not unready, and pulling the instance from rotation would turn a partial
outage into a total one.

## What to alert on

**Page.** `dead_letter_total` rising · `mongo_write_failures_total` non-zero · `/readyz`
failing.

**Ticket.** `projection_lag_seconds` above a minute · `ingest_rejected_backpressure_total`
sustained · `cache_fallback_total` sustained · `duplicate_conflict_total` non-zero.

**Never alert on** `duplicate_suppressed_total`. It is the system working correctly under
at-least-once delivery.
