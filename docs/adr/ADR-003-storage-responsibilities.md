# ADR-003: MongoDB is canonical; Elasticsearch and Redis are derived projections

## Status

Accepted — 2026-08-31

## Context

The assignment mandates three stores. The interesting question is not which to use but
which one is *right* when they disagree — and they will disagree, because they are updated
at different times by different code paths.

Without a stated answer, "the data" means whichever store the reader happened to query,
and every outage becomes a data-reconciliation problem.

## Decision

**MongoDB owns the truth. Everything else is a rebuildable opinion about it.**

| Store | Owns | Does not own | Consistency | Rebuildable from |
|---|---|---|---|---|
| MongoDB | Canonical events, idempotency constraint, projection state, dead letters, tenants | Search relevance, cached summaries | Source of truth | nothing — it *is* the truth |
| Elasticsearch | Searchable projection | Canonical event content | Eventually consistent | MongoDB, by reindex |
| Redis | Cached summaries, locks, rate counters | Any durable business data | Bounded stale (30s) | MongoDB, on next miss |

Consequences that follow mechanically:

- An Elasticsearch failure must never discard a stored event, and must never gate
  acknowledgement of the queue message.
- A Redis failure degrades latency, never correctness — the live summary is computed from
  MongoDB and marked `degraded: true`.
- Deleting the Elasticsearch index or flushing Redis is a recoverable operation, not data
  loss. The test suite does both between runs.

## Consequences

**Easier.** Every failure has an obvious answer: if MongoDB has it, it happened. Derived
stores can be rebuilt, resized, reindexed, or replaced without a migration plan.

**Harder.** Two read paths return different answers for a moment, and the API has to be
honest about it rather than pretending otherwise. `GET /events/search` carries an explicit
`freshness_note`; `GET /events/stats/realtime` returns `cached` and `age_seconds` so a
caller can judge the number they were given.

**Harder.** Projection failures need their own recovery path, which is ADR-005.

**Rejected: Elasticsearch as the query store for everything.** Fewer moving parts and
faster reads. But then event truth lives in a store with no unique constraint, no
transactional write, and a refresh interval — and idempotency (ADR-002) has nothing to
stand on.

**Rejected: writing all three synchronously before acknowledging.** It looks safer and is
worse: the slowest and least available store becomes the availability floor for ingestion,
so an Elasticsearch hiccup stops event capture entirely.
