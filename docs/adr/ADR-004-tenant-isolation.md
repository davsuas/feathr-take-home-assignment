# ADR-004: Enforce tenant isolation structurally, across storage, query, and cache

## Status

Accepted — 2026-08-31

## Context

Customers are distinct nonprofits, associations, and chapters. Their event data includes
constituent behaviour and donation activity. One customer seeing another's data is not a
bug to be fixed in the next release; it is the failure that ends the relationship.

Isolation enforced by convention fails eventually. Someone writes a query without the
tenant filter, review misses it because the line looks like every other query, and the
leak is silent — nothing errors, a caller simply receives more than they should.

The design question is therefore not "will we filter by tenant" but "what makes forgetting
to filter *impossible* rather than merely discouraged".

## Decision

Four mechanisms, each closing a different gap.

**1. Tenant identity comes from a credential, never from the request.** The caller presents
an opaque bearer token; the platform maps its SHA-256 hash to exactly one tenant. There is
no request field through which a caller can name a tenant — the ingestion schema forbids
extra fields, so an attempt is a loud `422` rather than a silently ignored key.

**2. `TenantContext` is a required first argument on every tenant-touching port method.**
Not ambient state, not a context variable, not a default. Forgetting to scope a query is a
type error at authoring time rather than a disclosure at runtime. This is the single most
load-bearing decision in this ADR.

**3. Every storage-level construct leads with the tenant.**
- MongoDB: all five indexes begin with `tenant_id`; the unique constraint is
  `(tenant_id, event_id)`.
- Elasticsearch: the query builder adds `filter: [{term: {tenant_id}}]` itself. There is no
  parameter through which a caller supplies a query body.
- Redis: keys are `v1:{tenant_id}:...`, with the tenant as a literal segment so an operator
  reading Redis by hand can see who owns a key.

**4. Non-existence and not-yours are indistinguishable.** An unknown credential and a
revoked one return identical responses. Another tenant's event id returns exactly what a
fabricated id returns. Existence cannot be probed across the boundary.

## Consequences

**Easier.** A future shard key on `tenant_id` is a configuration change, because every
index already leads with it. Cache entries are visibly tenant-owned during incident
triage.

**Harder.** Every port signature carries a `tenant` argument, including places where it
feels redundant. That verbosity is the mechanism, not a side effect — the moment it becomes
optional, the guarantee becomes a convention again.

**Harder.** The reconciler legitimately works across tenants, since it re-indexes what is
already stored rather than returning data to a caller. That exception is documented in the
repository method itself so it is not mistaken for an oversight.

**Rejected: an `X-Tenant-Id` header.** Trivial to run and demo, and spoofable — which
makes the isolation tests prove scoping but not authorization. Spec decision D1 chose the
credential precisely so SC-005 tests something real.

**Rejected: ambient tenant via `contextvars`.** Removes the argument from every signature
and reintroduces exactly the failure mode being designed against: a query written without
the scope still compiles and still runs.

**Production direction.** The credential-to-tenant mapping is seeded for the review
environment. Issuance, rotation, and revocation workflows, and a JWT carrying a verified
tenant claim from a real identity provider, are the production evolution — named here as a
limitation rather than left implied.
