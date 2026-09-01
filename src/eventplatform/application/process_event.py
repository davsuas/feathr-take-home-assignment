"""Worker-side processing (FR-011..FR-015).

Two decisions live here:

1. All three insert outcomes acknowledge the message. A duplicate is a normal result of
   at-least-once delivery, not a failure - the event is durably resolved either way, and
   refusing to acknowledge would loop the same message forever.
2. Projection happens after acknowledgement. A derived store must never gate canonical
   persistence, so an Elasticsearch outage delays search without stalling ingestion.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from eventplatform.domain.event import Event, Projection, ProjectionStatus
from eventplatform.domain.identity import TenantContext
from eventplatform.domain.policies.idempotency import InsertOutcome
from eventplatform.ports.clock import Clock
from eventplatform.ports.event_repository import EventRepository
from eventplatform.ports.event_search_index import EventSearchIndex
from eventplatform.ports.metrics import Metrics

_OUTCOME_COUNTERS = {
    InsertOutcome.DUPLICATE_SUPPRESSED: "duplicate_suppressed_total",
    InsertOutcome.DUPLICATE_CONFLICT: "duplicate_conflict_total",
}


@dataclass(frozen=True, slots=True)
class ProcessingResult:
    outcome: InsertOutcome
    projected: bool
    projection_error: str | None = None


class ProcessEvent:
    def __init__(
        self,
        *,
        repository: EventRepository,
        search_index: EventSearchIndex | None,
        metrics: Metrics,
        clock: Clock,
    ) -> None:
        self._repository = repository
        self._search = search_index
        self._metrics = metrics
        self._clock = clock

    async def __call__(self, tenant: TenantContext, event: Event) -> ProcessingResult:
        self._metrics.increment("processing_attempts_total")
        outcome = await self._repository.insert(tenant, event)

        counter = _OUTCOME_COUNTERS.get(outcome)
        if counter:
            self._metrics.increment(counter)

        if outcome is InsertOutcome.DUPLICATE_CONFLICT:
            # First write wins (FR-014). Recorded, not raised: raising would dead-letter
            # an event whose canonical version is already correctly stored.
            return ProcessingResult(outcome=outcome, projected=False)

        if outcome is InsertOutcome.DUPLICATE_SUPPRESSED:
            return ProcessingResult(outcome=outcome, projected=False)

        projected, error = await self._project(tenant, event)
        return ProcessingResult(outcome=outcome, projected=projected, projection_error=error)

    async def _project(self, tenant: TenantContext, event: Event) -> tuple[bool, str | None]:
        if self._search is None:
            return False, None
        now: datetime = self._clock.now()
        try:
            await self._search.index(tenant, event)
        except Exception as exc:
            self._metrics.increment("projection_failures_total")
            await self._repository.update_projection(
                tenant,
                event.event_id,
                Projection(
                    status=ProjectionStatus.FAILED,
                    attempts=event.projection.attempts + 1,
                    last_error=type(exc).__name__,
                    updated_at=now,
                ),
            )
            return False, type(exc).__name__
        await self._repository.update_projection(
            tenant,
            event.event_id,
            Projection(
                status=ProjectionStatus.INDEXED,
                attempts=event.projection.attempts + 1,
                last_error=None,
                updated_at=now,
            ),
        )
        return True, None
