# Data Model

Per-store ownership, the event envelope, indexes, mappings, and key structure.
Decision rationale lives in [`adr/`](adr/) and
[`../specs/001-distributed-event-platform/research.md`](../specs/001-distributed-event-platform/research.md).

## Store ownership

| Store | Owns | Does not own | Consistency class | Rebuildable from |
|---|---|---|---|---|
| MongoDB | Canonical immutable events, idempotency constraint, projection state, dead letters, tenant directory | Search relevance, cached summaries | Source of truth | nothing — it *is* the truth |
| Elasticsearch | Searchable projection of events | Canonical event content | Eventually consistent, lag bounded by SC-011 | MongoDB, by full reindex |
| Redis | Cached live summaries, single-flight locks, rate-limit counters | Any durable business data | Bounded stale, TTL 30 s | MongoDB, on next miss |
| Queue | Pending delivery state | Long-term storage | At-least-once | not rebuildable — see limitation below |
| Dead-letter store | Exhausted events plus failure diagnostics | Silent deletion | Durable, operator-replayable | nothing |

**Limitation stated plainly**: with the in-process queue adapter, messages accepted but not yet
processed are lost on process restart. The RabbitMQ adapter with quorum queues removes this; SQS
would too. This is the single most important honesty point in the whole design and belongs in
`docs/failure-modes.md` row one.

## Domain entities

### Event (canonical, immutable)

| Field | Type | Required | Rules | Source |
|---|---|---|---|---|
| `event_id` | string, ULID-shaped, ≤64 | yes | Sender-supplied when present, platform-generated when absent (D2); unique per tenant | FR-006 |
| `tenant_id` | string | yes | Never from the request body — resolved from the credential (D1) | FR-032 |
| `event_type` | string enum | yes | From the catalog: `pageview`, `click`, `form_submit`, `conversion`, `email_open`, `email_click`, `ad_impression`, `ad_click`, `registration`, `donation`, `membership`, `renewal` | FR-006 |
| `occurred_at` | datetime UTC | yes | Within −90 days and +5 minutes of now (R15) | FR-009, FR-006 |
| `received_at` | datetime UTC | yes | Server-assigned, never client-supplied | FR-006 |
| `schema_version` | int | yes | 1 | FR-006, FR-042 |
| `source` | object | yes | `{producer, ip_hash, user_agent_family}` — provenance, no raw IP | FR-006, FR-036 |
| `anonymous_id` | string, ≤128 | no | Independent of `contact_id` | FR-007 |
| `contact_id` | string, ≤128 | no | Independent of `anonymous_id` | FR-007 |
| `session_id` | string, ≤128 | no | | FR-007 |
| `campaign_id` | string, ≤128 | no | | FR-008 |
| `channel` | string enum | no | `web`, `email`, `ad`, `social`, `sms`, `offline` | FR-008 |
| `source_url` | string, ≤2048 | no | Absolute URL; query string retained, fragment dropped | FR-008 |
| `conversion` | object | no | `{goal_id, value: decimal ≥ 0, currency: ISO-4217}` | FR-008 |
| `consent` | object | no | `{analytics: bool, advertising: bool}`; absent ≠ false | FR-043, FR-044 |
| `metadata` | object | no | ≤16 KB serialized, ≤3 levels deep, ≤50 keys, scalar/array/object values only — no functions, no binary | FR-004, FR-005 |
| `content_hash` | string | yes | SHA-256 of the canonical envelope; derived, used for conflict detection (R4) | FR-014 |
| `projection` | object | yes | `{status: pending\|indexed\|failed, attempts, last_error, updated_at}` (R5) | FR-038 |

**Invariant**: after insert, no code path updates any field except `projection`. Enforced by
exposing only `insert`, `find`, and `update_projection` on the repository port — there is no general
update method to misuse.

### Tenant

`{tenant_id, name, status: active|suspended, created_at}` — with credentials in a separate
collection so a tenant read never carries a secret.

### Access Credential

`{key_hash: sha256, tenant_id, label, status: active|revoked, created_at, last_used_at}`. The
plaintext key exists only at issuance. Lookup is by `key_hash`; a revoked or unknown hash is
rejected identically to an absent one, so probing cannot distinguish the two.

### Dead-letter record

`{tenant_id, event_id, payload, failure_reason, failure_class, attempts, first_failed_at,
last_failed_at, replayed_at?}`. Carries the full original payload so replay needs no reconstruction
(FR-018).

## MongoDB collections and indexes

```text
events            see index table in ADR-003 and research R10 — five indexes, all led by tenant_id
dead_letters      {tenant_id: 1, last_failed_at: -1}, {tenant_id: 1, event_id: 1}
tenants           {tenant_id: 1} unique
credentials       {key_hash: 1} unique, {tenant_id: 1}
```

MongoDB runs standalone — no replica set. Idempotency needs only a unique index and
single-document atomicity; nothing in this design uses transactions or change streams (ADR-005).

## Elasticsearch index `events-v1`

`dynamic: strict`. Envelope fields mapped explicitly; `metadata` as `flattened`; `metadata_text` as
an analyzed copy of an allowlisted subset. Full rationale and the trade-off in ADR-003 and research R7.

```text
tenant_id, event_id, event_type, anonymous_id, contact_id,
session_id, campaign_id, channel, conversion.goal_id      keyword
occurred_at, received_at                                   date
conversion.value                                           scaled_float (scaling_factor 100)
source_url                                                 keyword + .text sub-field
metadata                                                   flattened
metadata_text                                              text (standard analyzer)
```

Every query is built as `bool.filter[{term: {tenant_id}}] + bool.must[user terms]`. The tenant
filter is added by the query builder, not by the caller, and there is no code path that constructs a
query without it.

## Redis keys

| Key | Purpose | TTL |
|---|---|---|
| `v1:{tenant_id}:stats:rt:{filter_hash}` | Cached live summary | 30 s |
| `v1:{tenant_id}:lock:stats:{filter_hash}` | Single-flight recomputation lock | 5 s |
| `v1:{tenant_id}:rl:{credential_hash}:{window}` | Rate-limit counter | window length |

`filter_hash` is `sha256` over the normalized filter set — sorted keys, canonical value encoding, so
two semantically identical requests hash identically and two different ones never collide into a
shared entry (FR-028). Tenant is a literal key segment, not part of the hash, so a key is visibly
tenant-owned when an operator inspects Redis by hand.

## State transitions

```text
Event:        (accepted) --> stored --> projection pending --> indexed
                                    \-> projection failed --> (reconciler) --> pending
Delivery:     queued --> in-flight --> acknowledged
                              \-> failed --> retry(1..5, backoff+jitter) --> dead-lettered
Duplicate:    in-flight --> duplicate suppressed (hash matches, no write)
                        \-> duplicate conflict (hash differs, first kept, conflict recorded)
```
