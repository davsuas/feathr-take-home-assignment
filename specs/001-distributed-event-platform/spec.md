# Feature Specification: Multi-Tenant Distributed Event Processing Platform

**Feature Branch**: `CORE-001`

**Created**: 2026-08-31

**Status**: Draft

**Input**: User description: "" (empty). Feature derived from `docs/Feathr-Take-Home.md`
(Principal Backend Software Engineer take-home: Distributed Event Processing Platform) and
`.specify/memory/constitution.md` (v1.0.0 at spec time; amended to v1.1.0 during
planning), which together define the single deliverable for this
repository. See Assumptions for the derivation record.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Capture engagement events without slowing the source (Priority: P1)

A marketing organization's website, ad tags, and email links emit engagement events (page views,
clicks, form submissions, donations). The sending system fires an event and must get an immediate
answer so the visitor's page is never held up. Some time later, an analyst for that same
organization looks up those events and sees them, complete and attributed to their organization
only.

**Why this priority**: This is the end-to-end reason the platform exists. Without accept-then-store
plus a tenant-scoped read-back, nothing else has value. It is the minimum viable slice.

**Independent Test**: Send a valid event for one organization, observe an immediate acceptance
response, then poll the event list for that organization until the event appears with every field
preserved. Delivers value on its own: durable capture plus retrieval.

**Acceptance Scenarios**:

1. **Given** a valid event for organization A, **When** it is submitted, **Then** the sender
   receives an acceptance receipt carrying the event's identifier without waiting for storage.
2. **Given** an accepted event, **When** background processing completes, **Then** the event is
   retrievable in organization A's event list with its type, timestamps, identity, source URL,
   and metadata unchanged.
3. **Given** an event whose payload fails validation, **When** it is submitted, **Then** it is
   rejected immediately with a message naming the offending field, and it is never stored.
4. **Given** events belonging to organization B, **When** organization A lists or filters events,
   **Then** zero organization B events are returned under any filter combination.
5. **Given** a stored event set, **When** a caller filters by event type, date range, identity, or
   source URL, **Then** only matching events for that organization are returned, in stable
   pagination order.
6. **Given** a request presenting no credential or an unrecognized credential, **When** it is
   submitted, **Then** it is rejected as unauthenticated, no event is accepted, and no
   organization's data is returned.

---

### User Story 2 - Never lose or double-count an event (Priority: P2)

The pipeline redelivers work after timeouts and crashes. The organization's reported donation
count must not inflate because a message was delivered twice, and a transient storage outage must
not silently drop a supporter's action.

**Why this priority**: Correctness of the numbers is the product. A platform that double-counts
conversions or loses them is worse than no platform, because decisions are made on the output.

**Independent Test**: Deliver the same event three times, confirm exactly one stored record and a
recorded duplicate-suppression count. Separately, force the store to fail, confirm the event is
retried with growing delays and eventually preserved for inspection rather than discarded.

**Acceptance Scenarios**:

1. **Given** an event already stored, **When** the identical event is delivered again, **Then**
   exactly one record exists, the redelivery is counted as a suppressed duplicate, and no error is
   surfaced to the sender.
2. **Given** the canonical store is temporarily unavailable, **When** processing is attempted,
   **Then** the attempt is retried a bounded number of times with increasing, randomized delays.
3. **Given** an event that fails every permitted attempt, **When** the last attempt fails, **Then**
   the event is preserved in a failed-event holding area together with the failure reason, attempt
   count, and timestamps, and is available for inspection and replay.
4. **Given** processing is interrupted after storage but before acknowledgement, **When** the work
   is redelivered, **Then** the outcome is still exactly one stored record.
5. **Given** two different events submitted with the same identifier by the same organization,
   **When** both are processed, **Then** the first is kept, the second is rejected as a conflicting
   duplicate, and the conflict is recorded.
6. **Given** a sender that received no response and retries its submission carrying the same
   sender-supplied identifier, **When** both submissions are processed, **Then** exactly one record
   exists and the retry is counted as a suppressed duplicate.

---

### User Story 3 - Understand engagement over time (Priority: P3)

A campaign manager asks how many page views, clicks, and conversions occurred per day over the
last month, so they can see whether an appeal is working.

**Why this priority**: Aggregate counts are the first analytical question every customer asks and
the primary justification for storing events at all. It depends on Story 1 but not on search or
caching.

**Independent Test**: Load a known set of events across several days and types, request counts
grouped by type and day, and assert the returned buckets match the known set exactly.

**Acceptance Scenarios**:

1. **Given** stored events across multiple days and types, **When** counts are requested grouped by
   type with a daily bucket over a date range, **Then** each bucket's count matches the underlying
   events for that organization only.
2. **Given** the same data, **When** the bucket size is changed to hourly or weekly, **Then**
   totals across buckets remain identical and only the grouping changes.
3. **Given** a request with no date range or a range wider than the permitted maximum, **When** it
   is submitted, **Then** it is rejected or clamped to the documented maximum span with an explicit
   indication of what was applied.
4. **Given** a bucket containing no events, **When** results are returned, **Then** the absence is
   represented unambiguously rather than silently omitted in a way that misleads.

---

### User Story 4 - Find events by what is inside them (Priority: P4)

A support engineer investigating a drop in mobile donations needs to find events whose metadata
mentions a particular browser, device, referrer campaign, or feature flag — text that is not a
fixed column.

**Why this priority**: Free-text investigation across flexible metadata is a distinct capability
from filtering on known fields, and it is valuable but not required for the core counting product.

**Independent Test**: Store events with distinctive metadata text, search for a term, and assert
matching events for the caller's organization are returned and non-matching or foreign-tenant
events are not.

**Acceptance Scenarios**:

1. **Given** events with searchable metadata, **When** a caller searches a term, **Then** matching
   events for their organization are returned with pagination and a total-match indication.
2. **Given** organization B events containing the same term, **When** organization A searches,
   **Then** none of organization B's events appear.
3. **Given** a caller supplies raw search-engine query syntax or operators, **When** the search is
   executed, **Then** the input is treated as literal search terms and cannot alter query
   structure, target other organizations, or reach unindexed fields.
4. **Given** an event was just accepted, **When** it is searched for immediately, **Then** it may
   not yet appear, and the response makes the freshness expectation explicit rather than implying
   the event does not exist.
5. **Given** the search capability is unavailable, **When** a search is attempted, **Then** the
   caller receives an explicit unavailable response, and no stored event is lost or altered.

---

### User Story 5 - Answer "what is happening right now" instantly (Priority: P5)

A dashboard polls a lightweight live summary for an organization on every page load. It must return
in a few milliseconds even under repeated polling, and it must not hammer the analytical store.

**Why this priority**: This is a latency and cost optimization over Story 3's numbers. It is
genuinely valuable but is the last capability whose absence still leaves a working product.

**Independent Test**: Request the live summary twice; assert the first request computes and stores
a summary, the second is served from the stored summary without recomputation, and the stored
summary expires after the configured freshness window.

**Acceptance Scenarios**:

1. **Given** no stored summary exists, **When** the live summary is requested, **Then** it is
   computed from stored events, returned, and retained for reuse.
2. **Given** a retained summary within its freshness window, **When** the same request is repeated,
   **Then** it is served from the retained summary without recomputation and is marked as such,
   along with the age of the data.
3. **Given** the freshness window has elapsed, **When** the summary is requested, **Then** it is
   recomputed and the retained copy is replaced.
4. **Given** two organizations request live summaries with identical filters, **When** both are
   served, **Then** each receives only its own data and neither can be served the other's retained
   summary.
5. **Given** the fast-summary capability is unavailable, **When** the live summary is requested,
   **Then** the platform still answers by computing from stored events, marks the response as
   degraded, and never fails the request outright solely because the fast path is down.

---

### User Story 6 - Operate the platform under stress (Priority: P6)

An on-call engineer needs to see backlog, failures, and duplicate suppression, and needs the
platform to refuse work honestly when it cannot accept more rather than accepting and discarding.

**Why this priority**: Operability is what makes the previous stories trustworthy in production,
but the platform can be demonstrated without it. It is last only in build order, not in importance.

**Independent Test**: Fill the intake backlog to its configured limit, submit another event, and
assert an explicit rejection with a retry indication and zero accepted-but-discarded events;
separately, assert the operational counters move as events are accepted, retried, suppressed, and
dead-lettered.

**Acceptance Scenarios**:

1. **Given** the intake backlog is at its configured capacity, **When** a new event is submitted,
   **Then** it is rejected with an explicit retry-later response, and no acceptance receipt is
   issued for it.
2. **Given** events flow through the platform, **When** an operator inspects operational signals,
   **Then** accepted counts, validation failures, backlog depth, processing attempts, retries,
   duplicate suppressions, failed-event count, storage failures, indexing lag or failures, and
   fast-summary hit/miss/fallback behaviour are all observable.
3. **Given** any recorded diagnostic output, **When** it is inspected, **Then** it carries
   correlation identifiers linking request, event, organization, and worker, and contains no raw
   personal data or full payload dumps.
4. **Given** a dependency is unavailable, **When** the platform's health is checked, **Then** the
   check distinguishes "not ready to accept work" from "process alive" rather than reporting a
   single opaque state.

---

### Edge Cases

- An event's stated occurrence time is in the future, or years in the past — is it accepted,
  clamped, or rejected, and does it distort time-bucketed counts?
- Two events arrive with the same identifier but different bodies — which wins, and is the conflict
  visible?
- Metadata is enormous, deeply nested, or contains unsupported value types — is it rejected before
  it can bloat storage and search?
- Metadata field names vary without limit across a customer's events, threatening unbounded growth
  in the search index's field set.
- A caller requests a page size or date range large enough to exhaust memory or time out.
- A caller supplies an identifier belonging to another organization to probe for existence.
- Processing succeeds in the canonical store but fails to reach the search index — the event must
  remain retrievable and the indexing gap must be recoverable, not silent.
- The canonical store is unavailable while intake is still accepting — how long before intake pushes
  back?
- A single malformed event repeatedly poisons a worker and blocks the backlog behind it.
- A worker is stopped mid-flight; in-flight work must not vanish, and any loss window must be
  documented rather than assumed absent.
- A live summary is requested for a filter combination that has never been computed, during a
  traffic spike, causing many simultaneous recomputations of the same answer.
- A sender omits an event identifier and then retries after a timeout: the platform generates a
  different identifier each time, so the retry becomes a second event and inflates counts. Is that
  consequence documented, and are senders told how to avoid it?
- A credential is presented for one organization while the request body or filters reference
  another organization's identifiers.
- Requests arrive with no resolvable organization context, or with a credential that has been
  revoked.

## Requirements *(mandatory)*

### Functional Requirements

#### Event intake

- **FR-001**: System MUST accept event submissions and respond with an acceptance receipt only
  after the event passes validation and has been durably handed to the processing backlog.
- **FR-002**: System MUST NOT imply, in the acceptance response, that the event is already stored,
  searchable, or reflected in summaries.
- **FR-003**: System MUST reject events failing validation before they enter the backlog, naming the
  offending field and reason, without partial storage.
- **FR-004**: System MUST enforce documented limits on payload size, metadata depth, metadata key
  count, and permitted metadata value types, and MUST reject submissions exceeding them.
- **FR-005**: System MUST reject new submissions with an explicit retry-later response when the
  backlog cannot safely accept more work, and MUST NOT accept work it intends to discard.
- **FR-006**: System MUST record every event with an organization identifier, an event identifier,
  an event type, the time the event occurred, the time it was received, its provenance, and the
  schema version under which it was validated. The event identifier MUST be taken from the sender
  when one is supplied and generated by the platform when none is, and the acceptance response MUST
  make clear which of the two occurred.
- **FR-007**: System MUST accept and preserve, independently, an anonymous visitor identifier, a
  known contact identifier, and a session identifier, and MUST NOT require a known contact.
- **FR-008**: System MUST accept and preserve marketing attribution attributes including campaign,
  channel, source URL, conversion goal, and monetary value where supplied.
- **FR-009**: System MUST represent and return all timestamps in a single documented absolute time
  standard with an unambiguous serialization.
- **FR-010**: System MUST treat stored events as immutable; no interface may modify or delete a
  stored event's content after acceptance.

#### Processing, idempotency, and recovery

- **FR-011**: System MUST tolerate the same event being delivered for processing more than once and
  MUST produce exactly one stored record per organization-plus-event-identifier pair. This MUST
  hold whether the repeat originates from a sender retrying its own submission or from internal
  redelivery.
- **FR-012**: System MUST enforce that uniqueness at the storage layer, not only in application
  logic, so concurrent duplicate processing cannot both succeed.
- **FR-013**: System MUST count and expose duplicate suppressions as an observable signal.
- **FR-014**: System MUST treat a repeat identifier carrying different content as a conflict,
  retain the first-stored version, and record the conflict.
- **FR-015**: System MUST NOT remove work from the backlog until the canonical record is confirmed
  stored.
- **FR-016**: System MUST retry failed processing a bounded number of times using increasing,
  randomized delays.
- **FR-017**: System MUST preserve events that exhaust all attempts in an inspectable failed-event
  holding area, recording failure reason, attempt count, first-failure and last-failure times, and
  the originating organization.
- **FR-018**: System MUST allow failed events to be re-submitted for processing without manual data
  reconstruction.
- **FR-019**: System MUST prevent a single repeatedly failing event from indefinitely blocking
  progress of unrelated events.

#### Reading, aggregation, search, and summaries

- **FR-020**: Users MUST be able to list their organization's events filtered by event type, date
  range, visitor or contact identity, and source URL, in any combination.
- **FR-021**: System MUST paginate every list response, apply a documented maximum page size, and
  return results in a stable, repeatable order.
- **FR-022**: Users MUST be able to request counts grouped by event type over a chosen time bucket
  of hourly, daily, or weekly granularity.
- **FR-023**: System MUST require a bounded date range for aggregate requests and MUST reject or
  explicitly clamp ranges exceeding the documented maximum span.
- **FR-024**: Users MUST be able to run a free-text search across designated event metadata fields
  and receive matching events with pagination and a match count.
- **FR-025**: System MUST construct search queries from validated, structured parameters only and
  MUST NOT accept raw search-engine query syntax from callers.
- **FR-026**: System MUST restrict free-text analysis to a designated set of metadata fields and
  MUST prevent arbitrary customer-supplied field names from expanding the searchable field set
  without bound.
- **FR-027**: Users MUST be able to request a lightweight live summary that is served from a
  retained copy within a configured freshness window and recomputed once that window elapses.
- **FR-028**: System MUST include the organization identifier and every filter dimension affecting
  the result in the identity of any retained summary, so no two distinct requests can share one.
- **FR-029**: System MUST indicate, on responses served from a retained copy, that the data is
  retained and how old it is.
- **FR-030**: System MUST answer live-summary requests by computing from stored events, marked as
  degraded, when the fast retained path is unavailable.
- **FR-031**: System MUST validate and bound all date range, pagination, bucket, and search
  parameters before executing any query.

#### Tenant isolation and security

- **FR-032**: System MUST resolve the organization context for every request from an opaque
  credential presented by the caller and mapped by the platform to exactly one organization. System
  MUST reject requests presenting no credential or an unrecognized one, and MUST NOT permit a caller
  to name its own organization directly in a request.
- **FR-033**: System MUST scope every read, aggregate, search, and retained summary to the caller's
  resolved organization, with no interface permitting a cross-organization query.
- **FR-034**: System MUST return the same response for another organization's existing identifier as
  for a non-existent one, so existence cannot be probed across organizations.
- **FR-035**: System MUST source all configuration and secrets from the deployment environment and
  MUST NOT contain credentials, personal data, or production endpoints in the repository.
- **FR-036**: System MUST exclude raw payloads and direct personal data from diagnostic output,
  error messages, retained-summary identities, and search indexes.

#### Consistency, observability, and evolution

- **FR-037**: System MUST document, and MUST NOT contradict in its responses, that stored events
  become retrievable before they become searchable, and that live summaries may be stale within the
  configured freshness window.
- **FR-038**: System MUST keep a stored event retrievable even when its projection into the search
  capability fails, and MUST make that failure observable and retryable.
- **FR-039**: System MUST emit correlated diagnostic records for request, event, organization, and
  worker across the intake and processing path.
- **FR-040**: System MUST expose the operational signals named in User Story 6, Scenario 2.
- **FR-041**: System MUST distinguish readiness to accept work from process liveness in its health
  reporting.
- **FR-042**: System MUST version the event schema and MUST introduce a new version rather than
  silently altering the meaning of an existing field.

#### Consent signals

- **FR-043**: System MUST accept, preserve immutably, and return per-event consent signals
  (at minimum analytics and advertising) when the sender supplies them, and MUST record their
  absence distinctly from a negative signal.
- **FR-044**: System MUST NOT treat a consent signal as an authorization decision of its own; the
  sending organization remains responsible for its lawful basis. Any downstream enforcement built
  on these signals is out of scope and MUST be recorded as a named limitation.

### Key Entities

- **Organization (Tenant)**: The customer whose data is being captured — a nonprofit, association,
  chapter, or event organization. Owns every event, query result, aggregate, search result, and
  retained summary. The isolation boundary of the entire platform. Callers act on an organization's
  behalf only by presenting a credential the platform maps to it.
- **Access Credential**: An opaque secret held by a caller and mapped by the platform to exactly one
  organization. It establishes organization context for every request; it never carries an
  organization name the caller could alter.
- **Event**: An immutable record that something happened. Carries organization, identifier, type,
  occurrence time, receipt time, provenance, schema version, identity attributes (anonymous,
  contact, session), attribution attributes (campaign, channel, source URL, conversion goal,
  monetary value), flexible validated metadata, and the sender's consent signals as supplied.
- **Acceptance Receipt**: What a sender gets back on submission — the event identifier and a
  statement that the event was accepted for processing, explicitly not that it is stored.
- **Processing Attempt**: One try at turning an accepted event into a stored record. Carries an
  attempt number, an outcome, and a delay before the next attempt.
- **Failed Event Record**: An event that exhausted its attempts, retained with failure reason,
  attempt count, timing, and organization, for inspection and replay.
- **Aggregate Result**: Counts of events grouped by type across time buckets for one organization
  over a bounded range.
- **Search Result**: Events for one organization matching free-text terms over designated metadata
  fields, with a match count.
- **Retained Summary**: A precomputed lightweight answer for one organization and one exact filter
  combination, valid for a bounded freshness window, with a recorded age.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A sender receives an acceptance or rejection for a submitted event in under 100 ms at
  the 95th percentile while the platform sustains 1,000 events per second of intake.
- **SC-002**: 100% of accepted events become retrievable by their organization; across a sustained
  one-hour load test, the count of accepted events equals the count of stored events plus the count
  of preserved failed events, with zero unaccounted-for events.
- **SC-003**: Delivering every event three times produces exactly one stored record per event —
  0 duplicates across at least 10,000 events — and the suppressed-duplicate count equals the number
  of redeliveries.
- **SC-004**: With the canonical store forced offline for 60 seconds and then restored, 100% of
  events accepted during the outage are subsequently stored or preserved as failed events; none are
  lost.
- **SC-005**: Across at least 100 probe attempts spanning every read, aggregate, search, and summary
  capability, an organization retrieves zero records belonging to another organization, and cannot
  distinguish another organization's existing identifier from a non-existent one. Probes MUST
  include requests presenting one organization's credential while referencing another
  organization's identifiers.
- **SC-006**: Filtered event listing over a 30-day range returns the first page in under 500 ms at
  the 95th percentile with 10 million stored events present.
- **SC-007**: Grouped counts over a 30-day range return in under 2 seconds at the 95th percentile
  with 10 million stored events present, and match a directly counted control set exactly.
- **SC-008**: A repeated live-summary request within the freshness window returns in under 20 ms at
  the 95th percentile and is served without recomputation at least 95% of the time under steady
  polling.
- **SC-009**: With the fast-summary path disabled, 100% of live-summary requests still return a
  correct answer marked as degraded, and 0% fail outright.
- **SC-010**: With the search capability disabled, 100% of event submissions are still accepted and
  stored, and 100% of stored events remain retrievable and countable.
- **SC-011**: 95% of events are searchable within 5 seconds of acceptance under normal operation,
  and the documented freshness expectation is never contradicted by a response.
- **SC-012**: 100% of submissions exceeding documented payload, depth, key-count, or type limits are
  rejected before entering the backlog, and 0 such submissions reach storage.
- **SC-013**: When the backlog reaches capacity, 100% of excess submissions receive an explicit
  retry-later rejection and 0 receive an acceptance receipt that is later discarded.
- **SC-014**: An operator can determine, from operational signals alone and without reading source
  code, the current backlog depth, retry volume, duplicate-suppression count, failed-event count,
  and degraded-path usage.
- **SC-015**: A reviewer following the repository's written instructions can start every dependency
  and run the entire automated test suite with a single documented command, on a clean machine, in
  under 15 minutes.
- **SC-016**: Every reliability and isolation guarantee stated in the architecture documentation is
  backed by a named automated test, a named observable signal, or an explicitly documented
  limitation — with zero unbacked claims.

## Assumptions

- **Derivation of scope**: The `/speckit-specify` command was invoked with no description. The
  feature is therefore derived from the two authoritative documents already in the repository:
  `docs/Feathr-Take-Home.md` (the assignment) and `.specify/memory/constitution.md` v1.0.0. The
  entire assignment is treated as one feature, sliced into six independently testable and
  independently deliverable user stories rather than split across several specs.
- **Multi-tenancy is an intentional extension**: The assignment names only a "User ID". The
  constitution mandates organization-level isolation throughout. Where the two differ, the
  constitution governs, and the assignment's user identity is modelled as the visitor/contact
  identity within an organization.
- **Technology stack is a fixed external constraint, not a choice made here**: The assignment fixes
  the runtime, canonical store, search capability, cache, and simulated queue. This specification
  deliberately states capabilities rather than products; the mapping to concrete technologies
  belongs to the plan and the ADRs required by the constitution.
- **Backend only**: No user interface, visual design, or frontend deliverable is in scope. Callers
  are other systems and analysts using a programmatic interface.
- **Single deployment, local reviewability**: The target is one reviewable environment a reviewer
  can start locally, not a multi-region production deployment. Production evolution is a
  documentation deliverable, not an implementation deliverable.
- **Retention**: Events are retained for the life of the review environment. No retention,
  archival, or right-to-erasure workflow is implemented; erasure obligations are documented as a
  named limitation.
- **Consent and personal data**: Events are assumed to be collected under the customer
  organization's own lawful basis. The platform's obligations in scope are to avoid amplifying
  personal data into diagnostics, retained summary identities, and search indexes (FR-036), and to
  carry the sender's consent signals faithfully so a future enforcement layer has them (FR-043,
  FR-044). The platform does not act on those signals itself.
- **Freshness windows are configurable**: The live-summary freshness window and the search
  visibility lag are configuration values with documented defaults, not fixed constants.
- **Time bucket set**: Hourly, daily, and weekly buckets are sufficient; no calendar-aware
  month/quarter bucketing is in scope.
- **Volume targets**: The performance figures in Success Criteria assume roughly 1,000 events per
  second sustained intake and 10 million stored events — an order of magnitude above what a review
  environment needs, chosen so the design must reason about scale rather than trivially satisfy it.

### Resolved Decisions

Both questions raised during specification were answered on 2026-08-31 by accepting the
recommended defaults. Recorded here because each shapes the external contract and the tests.

- **D1 - Organization context resolution: opaque credential mapped server-side.** A caller presents
  an opaque credential; the platform maps it to exactly one organization. A caller can never name
  its own organization in a request. Chosen over a plain organization header because FR-032 through
  FR-034 and SC-005 are only meaningfully testable when asserting an organization is not something
  the caller can do. The credential-to-organization mapping is seeded for the review environment;
  credential issuance, rotation, and revocation workflows are a named limitation, not a
  deliverable. Applied in FR-032, User Story 1 Scenario 6, SC-005, and the Access Credential entity.
- **D2 - Event identifier origin: sender-supplied when present, platform-generated when absent.**
  Duplicate suppression keys on organization plus event identifier, so a sender that retries a
  timed-out submission with the same identifier is suppressed rather than double-counted. Senders
  may collide or forge identifiers within their own organization; FR-014 governs that case by
  keeping the first stored version and recording the conflict. Cross-organization collision is
  impossible because the identifier is only ever unique within an organization. The cost of the
  fallback is explicit: a sender that omits an identifier and retries produces two events, recorded
  as an edge case and to be documented in the event contract. Applied in FR-006, FR-011, and User
  Story 2 Scenario 6.
