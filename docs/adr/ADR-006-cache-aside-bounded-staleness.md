# ADR-006: Cache-aside Redis summaries with bounded staleness

## Status

Accepted — 2026-08-31

## Context

`GET /events/stats/realtime` is polled by dashboards on every page load. Computing it from
MongoDB each time means an aggregation per poll per viewer — the query is not expensive
once, but it is expensive at dashboard-refresh frequency across every customer.

The specific problem to solve: serve a number fast enough for a dashboard, without either
hammering the aggregation or serving something misleading.

## Decision

**Cache-aside, 30-second TTL, no write-path invalidation, single-flight on miss, and a
degraded path that always answers.**

- **Key**: `v1:{tenant_id}:stats:rt:{sha256(normalized_filters)[:16]}`. The tenant is a
  literal segment; every filter that changes the response is inside the hash. Two distinct
  requests therefore cannot collide onto one entry, and two identical requests hash the
  same regardless of key order or an explicitly-`None` filter.
- **TTL 30s**, chosen against the write rate: at 1,000 events/second, a 30-second window
  absorbs 30,000 writes into one recomputation per key. Short enough that a dashboard feels
  live; long enough that the cache actually caches.
- **No invalidation on write.** Under a write-heavy event stream, invalidating on write
  means invalidating continuously — a cache that never serves. Bounded staleness is the
  honest trade, and it is stated in the response rather than hidden.
- **Single-flight**: concurrent misses for one key are collapsed by a short-lived Redis
  `SET NX` lock, so the stampede at expiry does not become N simultaneous aggregations.
- **Degraded path**: any Redis error is caught, the answer is computed from MongoDB, and
  the response carries `degraded: true`. The endpoint never fails because a derived store
  is down.

Every response carries `cached`, `age_seconds`, and `degraded`, so a caller can judge the
number they were handed instead of guessing.

## Consequences

**Easier.** Repeated polling costs one Redis `GET`. Losing Redis entirely costs latency,
not availability. Cache correctness is testable: `test_ttl_expiry_forces_recomputation`
inserts an event, asserts the stale answer is still served inside the window, then asserts
the refreshed answer after expiry.

**Harder — and stated plainly.** A number can be up to 30 seconds old. For a live
engagement dashboard that is correct behaviour; for anything requiring exactness, the
caller must use `GET /events/stats`, which reads MongoDB directly. The API documents this
rather than implying freshness it does not have.

**Rejected: write-through.** Every event write would update every affected summary key. The
fan-out is unbounded in the filter dimensions — window times event type times whatever
filters exist — so a single event could touch dozens of keys.

**Rejected: write-behind.** Puts durable business state in an evictable store, against
ADR-003.

**Rejected: invalidate-on-write.** As above: continuous invalidation under a continuous
write stream.

**Rejected: explicit invalidation with no TTL.** Any missed invalidation becomes permanent
staleness with no self-correction. A TTL is a bound that holds even when the invalidation
logic is wrong.

**At higher write volume.** The TTL stops being the lever once recomputation itself is too
slow. The path is pre-aggregated rollups maintained by the worker, with the cache serving
rollup reads rather than raw aggregations — noted in the scaling analysis.
