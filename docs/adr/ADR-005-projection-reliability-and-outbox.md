# ADR-005: Projection reliability — status field plus reconciler, outbox as the evolution

## Status

Accepted — 2026-08-31

## Context

ADR-003 makes Elasticsearch a derived projection. That raises the obvious question: what
happens to the projection when Elasticsearch is down at the moment an event is stored?

The naive answer — index inline, log the failure, move on — loses the event from search
permanently. Nothing records that the projection was owed, so nothing can retry it. The
event is safely in MongoDB and invisible to search forever, which is the worst kind of
failure: silent, and only discovered when a customer asks why their search is missing a
week of data.

The textbook answer is the transactional outbox: write the event and an outbox row in one
transaction, then have a separate reader drain the outbox. In MongoDB that means a
multi-document transaction or a change stream, and both require a replica set.

## Decision

**Projection state lives on the event document itself.**

```
projection: { status: pending | indexed | failed, attempts, last_error, updated_at }
```

The worker inserts the event, acknowledges the message, attempts the projection, then
updates `projection`. A background reconciler scans for `status` in `pending | failed`
older than a threshold and retries, backed by a partial index that costs nothing once
projections succeed.

Because the status lives on the event document, MongoDB's single-document atomicity gives
the write and its projection state a consistent update — with **no transaction, and
therefore no replica set**. MongoDB runs standalone in both Compose and the test suite.

## Consequences

**Easier.** "Search catches up after an Elasticsearch outage" is a demonstrable behaviour,
covered by `test_reconciler_repairs_the_projection_once_search_returns` and by the
quickstart walkthrough. Compose loses an init container and a class of startup race.

**Harder.** The reconciler is a second moving part in the worker. It is roughly forty
lines, and it must never die — a failed cycle is logged and the loop continues.

**A real limitation.** This is not a true outbox. The event insert and the projection
attempt are separate operations, so a worker that crashes between them leaves the event
`pending` until the reconciler notices. The window is bounded by the reconciler interval,
not by anything stronger. That is acceptable because the projection is derived and
rebuildable; it would not be acceptable for a canonical write.

**Rejected: a true transactional outbox with a change-stream reader.** The correct
production answer, and the evolution path if this system grows: a separate `outbox`
collection written in the same transaction, drained by a reader tailing the change stream.
It requires a replica set and a second process for no additional guarantee at this scale —
the reconciler already recovers every failure mode the outbox would.

**Rejected: index before acknowledging.** Makes a derived store's availability gate
canonical persistence, inverting ADR-003 and turning an Elasticsearch outage into an
ingestion outage.

**Rejected: fire-and-forget projection.** The failure described in Context. No path back.
