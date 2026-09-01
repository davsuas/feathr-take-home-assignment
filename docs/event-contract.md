# Event Contract

**Schema version 1** · Envelope, validation, and the policies a sender needs to know.

## Envelope

```json
{
  "event_id": "evt_01JXYZ...",
  "event_type": "conversion",
  "occurred_at": "2026-08-31T20:30:00Z",
  "anonymous_id": "anon_...",
  "contact_id": "contact_...",
  "session_id": "session_...",
  "campaign_id": "campaign_...",
  "channel": "email",
  "source_url": "https://organization.example/events/conference",
  "conversion": { "goal_id": "conference_registration", "value": 299.00, "currency": "USD" },
  "consent": { "analytics": true, "advertising": false },
  "metadata": { "browser": "Safari", "device_type": "mobile" }
}
```

The platform adds `tenant_id`, `received_at`, `schema_version`, `content_hash`, and
`projection` on receipt. None of those may be supplied by a sender.

## Fields

| Field | Required | Type | Rules |
|---|---|---|---|
| `event_id` | no | string ≤64 | **Supply this.** See "The identifier decision" below. |
| `event_type` | **yes** | enum | Closed catalogue, below |
| `occurred_at` | **yes** | ISO 8601 UTC | Within −90 days and +5 minutes of server time |
| `anonymous_id` | no | string ≤128 | Independent of `contact_id` |
| `contact_id` | no | string ≤128 | Independent of `anonymous_id` |
| `session_id` | no | string ≤128 | |
| `campaign_id` | no | string ≤128 | |
| `channel` | no | enum | `web`, `email`, `ad`, `social`, `sms`, `offline` |
| `source_url` | no | string ≤2048 | |
| `conversion.goal_id` | no | string ≤128 | |
| `conversion.value` | no | number ≥0 | Negative is rejected |
| `conversion.currency` | no | string, 3 chars | ISO 4217 |
| `consent.analytics` | no | bool | **Absent ≠ false** |
| `consent.advertising` | no | bool | **Absent ≠ false** |
| `metadata` | no | object | ≤16 KB serialized, ≤3 deep, ≤50 keys, scalars/arrays/objects only |

Unknown fields are **rejected**, not ignored. A silently dropped field is data loss the
sender never learns about.

## Event type catalogue

`pageview` · `click` · `form_submit` · `conversion` · `email_open` · `email_click` ·
`ad_impression` · `ad_click` · `registration` · `donation` · `membership` · `renewal`

Closed on purpose. An open set lets a typo create a permanent new series in every
aggregate, and nothing can distinguish a typo from a new product event after the fact.
Adding a type is a code change and a schema-version consideration.

## The identifier decision — read this one

`event_id` is **sender-supplied when present, platform-generated when absent**.

Deduplication keys on `(tenant_id, event_id)`. The consequence is direct:

- **You supply an id.** Your timed-out retry carrying the same id is recognised as the same
  event. One record. This is what you want.
- **You omit it.** The platform generates a different id each time, so your retry becomes a
  **second event** and inflates your counts.

The acceptance response tells you which happened:

```json
{ "event_id": "evt_...", "status": "accepted", "event_id_origin": "sender" | "platform" }
```

Ids need only be unique within your organisation. A collision with another customer is
impossible.

**Conflicting duplicates.** If the same id arrives with different content, the first stored
version is kept and the conflict is recorded (`duplicate_conflict_total`). The platform
does not overwrite an event.

## Identity: anonymous and known are independent

A visitor browses anonymously (`anonymous_id`), then registers and becomes known
(`contact_id`). Both may be present, either may be absent, and neither implies the other.
The platform stores what you send and does **not** resolve one to the other — no identity
graph, no stitching. That belongs to a downstream system, and pretending otherwise would
invent links that were never observed.

## Timestamps

`occurred_at` is yours; `received_at` is always server-assigned and a supplied value is
ignored. That is what keeps ingestion-lag measurement honest — a client-supplied
`received_at` could hide lag.

The −90 day / +5 minute window exists because unbounded `occurred_at` lets one
misconfigured client rewrite historical aggregates, and that surfaces weeks later as an
inexplicable spike nobody can trace.

## Consent

Consent signals are **captured and preserved**, and the platform **does not act on them**.
`absent` is distinct from `false`: absent means the sender said nothing, false means the
sender recorded a refusal. Enforcement built on these signals is out of scope and named as
a limitation. The sending organisation remains responsible for its lawful basis.

## PII

Do not put direct personal data in `metadata`. Metadata reaches the search index and is the
field most likely to be inspected during debugging.

The platform's own obligations: no raw payload or metadata in logs (a structlog processor
drops a fixed key set), no personal data in cache keys, no raw IP stored — only a hash.

## Where each field lives

| Field | MongoDB index | Elasticsearch |
|---|---|---|
| `tenant_id` | Leads every index | `keyword`, filter on every query |
| `event_id` | Unique with `tenant_id` | `keyword` |
| `event_type` | Compound with tenant + time | `keyword` |
| `occurred_at` | Compound, descending | `date` |
| `contact_id`, `anonymous_id` | Sparse compound | `keyword` |
| `source_url` | **Not indexed** — see below | `keyword` + `.text` |
| `campaign_id`, `channel` | **Not indexed** | `keyword` |
| `metadata` | **Never indexed** | `flattened` + allowlisted `metadata_text` |

`source_url` and `campaign_id` are deliberately unindexed in MongoDB: within a tenant they
are low-selectivity, and queries using them almost always pair with a type or time filter
that an existing index already serves. `metadata` is never indexed in MongoDB — that is the
entire reason Elasticsearch is in the design.

## Schema versioning

`schema_version` is on every stored event. A breaking change gets a new version, never a
silent reinterpretation of an existing field. Adding an optional field is not breaking;
changing a field's meaning, type, or requiredness is.

## Consistency, stated once

- `202 Accepted` means validated and queued. **Not** stored, **not** searchable, **not** in
  any summary.
- `GET /events` reflects canonical storage.
- `GET /events/search` lags behind it; responses carry a `freshness_note`.
- `GET /events/stats/realtime` may be up to 30 seconds stale; responses carry `cached`,
  `age_seconds`, and `degraded`.
