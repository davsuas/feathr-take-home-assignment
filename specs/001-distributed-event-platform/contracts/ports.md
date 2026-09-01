# Port Contracts

**Plan**: [plan.md](../plan.md)

The eight interfaces the application layer depends on. Every one lives in `src/eventplatform/ports/`
and is a `typing.Protocol` — structural, so adapters need no import of the port and the dependency
arrow points inward only.

An `import-linter` contract in `tests/architecture/` fails the build if `domain/` or `application/`
imports fastapi, pymongo, redis, elasticsearch, aio_pika, or boto3. That test is what makes the
hexagonal claim in `ARCHITECTURE.md` verifiable rather than aspirational.

## The tenant-scope rule

Every method that touches tenant data takes `tenant: TenantContext` as a **required first
argument**. This is deliberate and structural: forgetting to scope a query becomes a type error at
authoring time rather than a cross-tenant disclosure at runtime. There is no ambient tenant, no
context variable, and no default. This is the strongest available enforcement of constitution
Principle I.3.

| Port | Methods | Notes |
|---|---|---|
| `EventQueue` | `publish`, `receive`, `acknowledge`, `retry_later`, `depth` | See [queue-message.md](./queue-message.md) |
| `EventRepository` | `insert(tenant, event) -> InsertOutcome`, `find_page(tenant, filters, cursor, limit)`, `aggregate_counts(tenant, range, bucket, event_type)`, `update_projection(tenant, event_id, state)`, `find_pending_projections(older_than, limit)` | **No general update and no delete** — immutability (FR-010) is enforced by the absence of the method, not by a rule someone must remember |
| `EventSearchIndex` | `index(tenant, event)`, `search(tenant, terms, filters, cursor, limit)`, `ensure_mapping()` | `search` takes structured terms, never a query body (FR-025) |
| `StatsCache` | `get(tenant, key)`, `set(tenant, key, value, ttl)`, `acquire_lock(tenant, key, ttl)`, `release_lock(...)` | Every method may raise `CacheUnavailable`; callers must degrade, not fail (FR-030) |
| `DeadLetterStore` | `record(tenant, event_id, payload, failure)`, `list(tenant, cursor, limit)`, `get(tenant, event_id)` | Replay reads from here and republishes |
| `TenantDirectory` | `resolve(credential_hash) -> TenantContext \| None` | Returns `None` identically for unknown and revoked (FR-034) |
| `Clock` | `now() -> datetime` | Injected so `occurred_at` window tests (R15) and TTL expiry tests are deterministic |
| `Metrics` | `increment(name, **labels)`, `observe(name, value, **labels)`, `gauge(name, value, **labels)` | Label sets are fixed per metric; `tenant_id` is a label only on counters where cardinality is bounded |

## InsertOutcome

`insert` returns a value rather than raising for the duplicate case, because a duplicate is a normal
and expected outcome under at-least-once delivery — not an error:

```python
class InsertOutcome(Enum):
    INSERTED = "inserted"  # first time seen
    DUPLICATE_SUPPRESSED = "duplicate_suppressed"  # same event_id, same content_hash
    DUPLICATE_CONFLICT = "duplicate_conflict"  # same event_id, different content_hash
```

The worker acknowledges the message for all three (the event is durably resolved in every case) and
increments a different counter for each. `DUPLICATE_CONFLICT` keeps the first-stored version
(FR-014).
