# Quality Attribute Scenarios

Measurable scenarios rather than adjectives. Each maps to the success criterion it proves
and to the test or metric that proves it.

**Status legend** — ✅ verified by an automated test · 📊 observable via a metric ·
⚠️ documented limitation, no test.

---

## QS-001: Ingestion latency (SC-001)

**Source** Tracking client · **Stimulus** Sustained submission · **Environment** Healthy
dependencies, queue capacity available · **Artifact** `POST /events`
**Response** Validate, enqueue, return · **Measure** p95 under 100 ms; response is 202

📊 `http_request_duration_seconds{route="/events"}`. The path holds no I/O beyond a queue
publish — no database write is on it, by design.
**Not load-tested.** See "What was not measured".

## QS-002: No event unaccounted for (SC-002)

**Source** Sustained ingestion · **Stimulus** 25 events submitted · **Environment** Healthy
· **Artifact** Full pipeline
**Response** Every accepted event is stored or dead-lettered · **Measure** accepted =
stored + dead-lettered, zero unaccounted

✅ `test_accepted_equals_stored_plus_dead_lettered`

## QS-003: Duplicate delivery (SC-003)

**Source** Queue redelivery and sender retry · **Stimulus** Same event delivered 3×, and 8
concurrent inserts · **Environment** Healthy · **Artifact** Worker + MongoDB
**Response** Exactly one canonical record · **Measure** 1 INSERTED, N−1 suppressed

✅ `test_triplicate_delivery_single_record`, `test_concurrent_duplicates_cannot_both_insert`,
`test_sender_retry_with_same_id_is_suppressed`,
`test_concurrent_submissions_of_one_event_yield_one_record`
📊 `duplicate_suppressed_total`

## QS-004: MongoDB outage (SC-004)

**Source** MongoDB unreachable · **Stimulus** Worker attempts to persist · **Environment**
Queue available · **Artifact** Worker
**Response** Not acknowledged; retried with backoff; dead-lettered after N · **Measure** no
acknowledged event silently lost

✅ `test_retry_then_dead_letter`, `test_transient_failure_is_retried_not_dead_lettered`,
`test_dead_letter_retains_the_complete_payload`
📊 `mongo_write_failures_total`, `processing_retries_total`, `dead_letter_total`

## QS-005: Tenant isolation (SC-005)

**Source** Authenticated tenant A · **Stimulus** Every read path · **Environment** Tenant B
holds matching values · **Artifact** Query, aggregate, search, cache
**Response** Only tenant A data · **Measure** zero foreign records; foreign id
indistinguishable from missing

✅ `test_isolation.py` (4 paths), `test_the_whole_pipeline` step 7,
`test_foreign_event_id_is_indistinguishable_from_a_missing_one`,
`TestCacheKeyNeverSharedAcrossTenants`

## QS-006/007: Query and aggregate latency (SC-006, SC-007)

**Measure** listing p95 under 500 ms, aggregation p95 under 2 s, at 10M events

📊 `http_request_duration_seconds`. Correctness is verified
(`test_ingest_to_stats_roundtrip`, `test_totals_are_identical_across_bucket_sizes`);
**latency at 10M events was not measured** — see below.

## QS-008: Cached summary latency (SC-008)

**Source** Dashboard polling · **Stimulus** Repeated identical request · **Environment**
Cache warm · **Artifact** `GET /events/stats/realtime`
**Response** Served from cache · **Measure** p95 under 20 ms; ≥95% served cached

✅ `test_cache_miss_then_hit`, `test_cached_response_reports_its_age`,
`test_ttl_expiry_forces_recomputation`
📊 `cache_hit_total` / `cache_miss_total`

## QS-009: Redis failure (SC-009)

**Response** Correct answer computed from MongoDB, `degraded: true` · **Measure** 100%
still answered, 0% failed

✅ `test_cache_unavailable_degrades`

## QS-010: Elasticsearch failure (SC-010, SC-011)

**Response** Ingestion and listing unaffected; search explicitly unavailable; reconciler
catches up · **Measure** zero canonical loss

✅ `test_search_unavailable_degrades`, `test_event_is_stored_even_when_search_is_down`,
`test_reconciler_repairs_the_projection_once_search_returns`
📊 `projection_lag_seconds`

## QS-012: Malformed input (SC-012)

**Response** Rejected before the queue with the field named · **Measure** 100% rejected,
0 reach storage

✅ `TestMetadataLimitsRejected`, `test_oversized_metadata_is_rejected_before_the_queue`,
`test_future_timestamp_is_rejected`, `test_unknown_field_is_rejected`

## QS-013: Backpressure (SC-013)

**Response** 503 with `Retry-After`; no receipt issued for rejected work · **Measure**
accepted count equals queue depth

✅ `test_backpressure_returns_503`

## QS-014: Operability (SC-014)

**Response** Every signal readable from `/metrics` without reading source

✅ `test_metrics_exposes_the_operational_signals` (13 signals asserted)

## QS-015: Reproducibility (SC-015)

**Response** One documented command runs everything

✅ `uv run pytest` — 126 tests, no manual setup

## QS-016: Claims are backed (SC-016)

**Response** Every guarantee maps to a test, a metric, or a stated limitation

✅ This document plus the 21-row map in `specs/001-distributed-event-platform/quickstart.md`

---

## What was **not** measured

Stated plainly rather than implied by omission, because an unbacked claim is worse than an
acknowledged gap.

**No load test was run.** SC-001, SC-006, SC-007, and SC-008 specify p95 latencies at 1,000
events/second and 10M stored events. The suite verifies *correctness* at every one of those
paths and the metrics to measure them are exposed, but the numbers themselves are
unverified. Treat them as design targets, not results.

The design reasoning behind each: ingestion holds no database write on the request path;
listing and aggregation are served by compound indexes led by `tenant_id` with bounded
ranges; the cached summary is a single Redis `GET`. Whether those hold at scale is a
measurement nobody has taken.

**Untested limitations** (`docs/failure-modes.md`): in-process queue durability across
restart, and the absence of any retention or erasure workflow.
