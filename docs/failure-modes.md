# Failure Modes

FMEA-lite. What breaks, what the platform does, whether anything is lost, and how it
recovers.

## The honest headline

**Row 1 is the most important line in this document.** With the in-process queue adapter,
messages accepted but not yet processed are lost when the process restarts. A `202
Accepted` that survives no further than process memory is not durable acceptance. This is
not excused — it is the reason the RabbitMQ adapter and ADR-007 exist.

## Dependency failures

| Failure | Platform behaviour | Data loss | Recovery | Signal |
|---|---|---|---|---|
| **API restart, in-process queue** | Queued-but-unprocessed messages are lost | **High in the demo. Unacceptable in production.** | Run the RabbitMQ adapter (`QUEUE_BACKEND=rabbitmq`) or SQS — both durable | `queue_depth` drops without matching stores |
| **API restart, RabbitMQ adapter** | Unacked messages redelivered on reconnect | None | Automatic | `queue_depth`, restart count |
| **MongoDB unavailable** | Worker retries with backoff; does **not** acknowledge. API `/readyz` returns 503 | None until queue capacity is exhausted, then explicit 503 rejections | Bounded retry (5, exponential + full jitter), then dead-letter with metadata | `mongo_write_failures_total`, `processing_retries_total`, `dead_letter_total` |
| **MongoDB slow** | Retries and backoff absorb it; queue depth grows | None until capacity | Backpressure surfaces as 503 before loss | `queue_depth`, `processing_duration_seconds` |
| **Elasticsearch unavailable** | MongoDB write succeeds and the event stays fully retrievable. `projection.status = failed`. `GET /events/search` returns explicit 503 | **None canonical.** Search is temporarily incomplete | Reconciler re-indexes every pending/failed projection once ES returns | `projection_failures_total`, `projection_lag_seconds`, `projection_reconciled_total` |
| **Elasticsearch mapping conflict** | `dynamic: strict` rejects the document loudly at index time | None canonical | Fix the mapping, reconciler replays | `projection_failures_total` |
| **Redis unavailable** | Live summary computed from MongoDB, response marked `degraded: true`. Never fails | None | Reconnects; cache repopulates on next miss | `cache_error_total`, `cache_fallback_total` |
| **Redis eviction under memory pressure** | Cache miss, recompute | None | Automatic | `cache_miss_total` rises |
| **Worker crashes mid-processing** | Message redelivered after visibility timeout | None | Idempotency makes the redelivery a no-op if the write landed | `duplicate_suppressed_total` |
| **Worker crashes between persist and ack** | Redelivered; second insert suppressed by unique index | None | Automatic — exactly-once *effects* | `duplicate_suppressed_total` |
| **Queue at capacity** | API returns 503 with `Retry-After`. No receipt issued | None — nothing is accepted then discarded | Sender retries; worker drains | `ingest_rejected_backpressure_total` |
| **Poison message** | Dead-lettered immediately, not retried | None — payload retained in full | Operator fixes the cause, replays via `POST /dead-letters/{id}/replay` | `dead_letter_total` with `failure_class=validation` |
| **Malformed/oversized payload** | Rejected at the API with the offending field named. Never queued | None — never accepted | Sender fixes and resubmits | `events_rejected_validation_total` |
| **Sender floods the API** | Token-bucket rate limiter returns 429 before validation | None | Automatic | `ingest_rejected_rate_limit_total` |
| **Clock skew on a sender** | ±5 min tolerated; beyond that rejected naming `occurred_at` | None — never accepted | Sender fixes its clock | `events_rejected_validation_total` |

## Blast radius by store

**MongoDB down** — the only dependency whose loss stops the platform accepting work.
`/readyz` returns 503; `/healthz` stays 200 so the orchestrator does not restart a healthy
process that is merely waiting.

**Elasticsearch down** — search unavailable; ingestion, listing, aggregation, and live
summaries all unaffected. Verified by `test_search_unavailable_degrades`.

**Redis down** — live summaries slower and marked degraded; everything else unaffected.
Verified by `test_cache_unavailable_degrades`.

That asymmetry is the point of ADR-003: only the canonical store is allowed to be critical.

## Known limitations, stated rather than hidden

These have **no automated test** and are documented limitations instead, as SC-016 requires:

1. **In-process queue durability across process restart.** Cannot be tested meaningfully
   without asserting the loss it describes. Fixed by running RabbitMQ or SQS.
2. **No retention, archival, or erasure workflow.** Events are kept for the life of the
   environment. A right-to-erasure request has no supported path, which matters given the
   constituent data this domain handles. A TTL index was deliberately rejected — it would
   silently delete canonical events and break immutability.
3. **In-process rate limiting.** Each API replica holds its own bucket, so the effective
   limit multiplies by replica count. A shared Redis counter is the production answer.
4. **Single-node stores.** No replication in Compose. Node loss is data loss for MongoDB.
5. **Worker metrics are not exposed over HTTP.** The worker has no `/metrics` endpoint, so
   its counters are not visible at the API's. Its structured logs carry the same
   information. See `docs/observability.md`.

## Operator runbook

**Events accepted but not appearing**
1. `queue_depth` — is the worker consuming?
2. `dead_letter_total` and `GET /dead-letters` — did they fail out?
3. `mongo_write_failures_total` — is the canonical store healthy?

**Search missing recent events**
1. `projection_lag_seconds` — how far behind?
2. `projection_failures_total` vs `projection_reconciled_total` — is the reconciler winning?
3. Expect a normal lag of seconds. Minutes means Elasticsearch is unhealthy.

**Live summary looks wrong**
1. Check `cached` and `age_seconds` in the response — up to 30s stale is correct behaviour.
2. `degraded: true` means Redis is down and the number was computed fresh — it is *more*
   accurate, not less.
3. For exact figures use `GET /events/stats`, which reads MongoDB directly.

**Backpressure (503s on ingest)**
1. `queue_depth` at capacity confirms it.
2. Scale workers — idempotency makes adding consumers safe by construction.
3. Rejection is correct behaviour. The alternative is accepting events and dropping them.
