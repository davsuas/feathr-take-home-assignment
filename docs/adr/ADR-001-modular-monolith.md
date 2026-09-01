# ADR-001: Use a modular monolith with hexagonal boundaries

## Status

Accepted — 2026-08-31

## Context

The assignment asks for discrete concerns: ingestion, processing, storage, querying, and
caching. It does not ask for independent deployment of any of them.

The pull toward microservices here is real and worth naming: an event platform is the
canonical distributed-systems example, and splitting the worker from the API looks like it
demonstrates distributed thinking. It would also let each part scale separately.

Against that: a one-week deliverable reviewed by a panel is evaluated on module boundaries
and trade-off reasoning, not on deployment topology. Splitting into services buys
independent scaling we do not need at review scale and costs networking, service
discovery, distributed tracing, multi-service local startup, and cross-service test
orchestration — all of which consume the time that should go into the boundaries
themselves.

## Decision

One deployable, four layers enforced as directories with a strict inward dependency rule:

```
api / worker  ->  application  ->  ports  <-  infrastructure
                                     |
                                   domain
```

- `domain` depends on nothing. No FastAPI, no driver, no framework.
- `application` depends on `ports` only — protocols describing what it needs.
- `infrastructure` implements those protocols.
- `api` and `worker` are entry points; `composition.py` is the only module that knows
  which concrete adapter is mounted.

The rule is enforced by `import-linter` (`.importlinter`) and by
`tests/architecture/test_boundaries.py`, both of which fail the build on violation.

## Consequences

**Easier.** The SQS migration of ADR-007 lands in one directory. Tests drive the domain
with no services running. A reviewer reads the boundary in the directory listing.

**Harder.** API and worker scale together in this deployment. Splitting them later means
extracting the worker entry point into its own image — real work, but bounded, because
`composition.py` already isolates the wiring.

**Verified, not asserted.** The boundary claim is a build failure, not a paragraph. During
implementation the contract caught `worker/main.py` importing the composition root from
`api/dependencies.py` — a genuine coupling that reads as harmless in review. The
composition root moved to `eventplatform/composition.py` as a result.

**Rejected: microservices.** Deferred until a documented constraint requires it —
independent scaling of ingestion versus query, or separate team ownership. Neither exists
at review scale, and constitution Principle V.7 forbids introducing them without one.

**Rejected: a layered monolith with direct driver imports.** Simpler to write, but then
"replace the queue" means editing every call site, and the SQS answer becomes a claim
nobody can check.
