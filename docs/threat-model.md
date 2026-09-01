# Threat Model (STRIDE-lite)

Marketing and fundraising telemetry carries constituent behaviour and donation activity.
Isolation and input safety are architectural concerns here, not a compliance checkbox.

## Trust boundaries

1. **Public internet → API.** Every sender is untrusted. Tags run on customer sites; their
   payloads are attacker-controllable in practice.
2. **API → queue.** Validated, tenant-stamped messages only.
3. **Worker → stores.** Trusted, but still tenant-scoped on every call.
4. **Operator → platform.** Same credential mechanism as any caller.

## STRIDE

| Threat | Concrete scenario | Mitigation | Verified by |
|---|---|---|---|
| **Spoofing** | A caller submits events attributed to another organisation | Tenant comes from an opaque credential mapped server-side; `tenant_id` is not an accepted request field, and the schema forbids extras, so an attempt is a loud 422 | `test_caller_cannot_name_its_own_tenant` |
| **Spoofing** | Credential enumeration to find valid keys | Unknown and revoked return byte-identical responses (modulo per-request trace id) | `test_unknown_and_revoked_credential_identical` |
| **Tampering** | Oversized or deeply nested metadata bloats every downstream store | 16 KB / 3 levels / 50 keys / scalar-array-object types, enforced **before** the queue | `TestMetadataLimitsRejected`, `test_oversized_metadata_is_rejected_before_the_queue` |
| **Tampering** | Backdated events rewrite historical aggregates | `occurred_at` bounded to −90 days / +5 minutes | `TestOccurredAtWindow` |
| **Tampering** | Overwriting a stored event by resubmitting its id | First write wins; conflicts recorded, never applied. No update or delete exists on the repository | `test_conflicting_duplicate_keeps_first`, `test_event_repository_exposes_no_mutation` |
| **Repudiation** | "What happened to my event?" cannot be answered | Correlation ids across API and worker; dead letters retain payload, reason, attempts, and timings | `test_correlation_ids_are_attached` |
| **Information disclosure** | Cross-tenant read via list, aggregate, search, or cache | `tenant_id` leads every index, is added by the search query builder itself, and is a literal cache-key segment. `TenantContext` is a required argument on every port method | `test_isolation.py` (4 paths), `test_the_whole_pipeline` step 7 |
| **Information disclosure** | Probing whether another tenant's event id exists | Foreign id and fabricated id return identically | `test_foreign_event_id_is_indistinguishable_from_a_missing_one` |
| **Information disclosure** | PII leaking into logs, cache keys, or the search index | Redaction processor drops a fixed key set; cache keys hold only tenant + filter hash; no raw IP is stored | `test_logging_redaction.py` |
| **Information disclosure** | Search injection widening scope | No raw DSL accepted; `multi_match` (never `query_string`) so operators are not parsed; tenant filter added by the builder | `test_raw_dsl_is_literal` (4 hostile inputs) |
| **Denial of service** | Event flood exhausts memory | Bounded queue, explicit 503 + `Retry-After`, token-bucket rate limit, publish timeout | `test_backpressure_returns_503` |
| **Denial of service** | Unbounded query exhausts the database | Page size capped at 200, aggregate range clamped, all params validated | `test_page_size_is_capped`, `test_range_wider_than_the_maximum_is_clamped_and_says_so` |
| **Denial of service** | Cache stampede at TTL expiry | Single-flight `SET NX` lock | `test_single_flight_lock_admits_one_holder` |
| **Elevation of privilege** | A credential reaching beyond its tenant | One credential maps to exactly one tenant; there is no cross-tenant query path in any interface | `test_isolation.py` |

## Secrets

Configuration comes from the environment. `.env.example` holds names and safe placeholders,
never values. API keys are stored only as SHA-256 hashes — a leaked database dump yields no
working credential. The plaintext exists once, at issuance.

## Non-goals, stated explicitly

- **No authorization model beyond the tenant boundary.** Every credential for a tenant can
  do everything that tenant can. Roles and scopes are production work.
- **No credential lifecycle.** Issuance exists via CLI; rotation and expiry do not.
- **No encryption at rest** beyond what the datastore provides by default.
- **No audit log** distinct from the operational log.
- **No consent enforcement.** Signals are captured faithfully and not acted upon
  (`docs/event-contract.md`).
- **No retention or erasure workflow.** The most significant gap for this domain, and the
  first thing to build after the take-home.
- **No mTLS or network policy** between containers in Compose.

## Highest residual risks

1. **In-process queue loses accepted events on restart** — ADR-007 is the fix.
2. **No erasure path** — constituent data with no deletion mechanism.
3. **Per-replica rate limiting** — the effective limit scales with replica count.
