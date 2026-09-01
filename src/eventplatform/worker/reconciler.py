"""Projection reconciler (FR-038, constitution IV.7).

Small on purpose. Inline projection alone loses the indexing attempt whenever
Elasticsearch is down mid-write, and the event is then permanently missing from search
with nothing pointing at it. This scans for that state and retries.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from eventplatform.domain.errors import SearchUnavailable
from eventplatform.domain.event import Projection, ProjectionStatus
from eventplatform.domain.identity import TenantContext
from eventplatform.infrastructure.observability.logging import get_logger
from eventplatform.ports.clock import Clock
from eventplatform.ports.event_repository import EventRepository
from eventplatform.ports.event_search_index import EventSearchIndex
from eventplatform.ports.metrics import Metrics

log = get_logger(__name__)


class ProjectionReconciler:
    def __init__(
        self,
        *,
        repository: EventRepository,
        search_index: EventSearchIndex,
        clock: Clock,
        metrics: Metrics,
        stale_after_seconds: float = 30.0,
        batch_size: int = 100,
    ) -> None:
        self._repository = repository
        self._search = search_index
        self._clock = clock
        self._metrics = metrics
        self._stale_after = stale_after_seconds
        self._batch_size = batch_size

    async def run_once(self) -> int:
        now = self._clock.now()
        stale_before = now - timedelta(seconds=self._stale_after)
        pending = await self._repository.find_pending_projections(
            stale_before=stale_before, limit=self._batch_size
        )
        if pending:
            oldest = min((e.projection.updated_at or e.received_at) for e in pending)
            self._metrics.gauge("projection_lag_seconds", (now - oldest).total_seconds())

        repaired = 0
        for event in pending:
            tenant = TenantContext(tenant_id=event.tenant_id, credential_hash="reconciler")
            try:
                await self._search.index(tenant, event)
            except SearchUnavailable:
                # The index is still down. Leave the marker in place and try again next
                # tick - there is nothing to escalate, the canonical event is safe.
                continue
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
            self._metrics.increment("projection_reconciled_total")
            repaired += 1

        if repaired:
            log.info("projections_reconciled", count=repaired)
        return repaired

    async def run_forever(self, *, interval_seconds: float = 15.0) -> None:
        while True:
            try:
                await self.run_once()
            except Exception as exc:
                log.warning("reconciler_cycle_failed", error=type(exc).__name__)
            await asyncio.sleep(interval_seconds)
