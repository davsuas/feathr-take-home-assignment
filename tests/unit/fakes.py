"""In-memory fakes for the ports, so worker semantics can be tested without services."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from eventplatform.domain.errors import CacheUnavailable
from eventplatform.domain.event import Event, Projection
from eventplatform.domain.identity import TenantContext
from eventplatform.domain.policies.idempotency import InsertOutcome, classify_duplicate
from eventplatform.ports.dead_letter_store import DeadLetter
from eventplatform.ports.event_repository import BucketCount, EventFilters, EventPage
from eventplatform.ports.event_search_index import SearchQuery, SearchResults
from eventplatform.ports.stats_cache import CachedValue


class FakeClock:
    def __init__(self, now: datetime | None = None) -> None:
        self._now = now or datetime(2026, 8, 31, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        from datetime import timedelta

        self._now += timedelta(seconds=seconds)


class RecordingMetrics:
    def __init__(self) -> None:
        self.counters: dict[str, int] = {}
        self.gauges: dict[str, float] = {}

    def increment(self, name: str, value: int = 1, **labels: str) -> None:
        self.counters[name] = self.counters.get(name, 0) + value

    def observe(self, name: str, value: float, **labels: str) -> None: ...

    def gauge(self, name: str, value: float, **labels: str) -> None:
        self.gauges[name] = value


class FakeRepository:
    """Mimics the unique-index behaviour of the real store, including the race."""

    def __init__(self, *, fail_times: int = 0) -> None:
        self.events: dict[tuple[str, str], Event] = {}
        self.fail_times = fail_times
        self.insert_calls = 0

    async def insert(self, tenant: TenantContext, event: Event) -> InsertOutcome:
        self.insert_calls += 1
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ConnectionError("mongodb unavailable")
        key = (tenant.tenant_id, event.event_id)
        if key in self.events:
            return classify_duplicate(
                incoming_hash=event.content_hash(),
                stored_hash=self.events[key].content_hash(),
            )
        self.events[key] = event
        return InsertOutcome.INSERTED

    async def get(self, tenant: TenantContext, event_id: str) -> Event | None:
        return self.events.get((tenant.tenant_id, event_id))

    async def find_page(
        self, tenant: TenantContext, filters: EventFilters, *, cursor: str | None, limit: int
    ) -> EventPage:
        items = [e for (t, _), e in self.events.items() if t == tenant.tenant_id]
        return EventPage(items=items[:limit], next_cursor=None)

    async def aggregate_counts(
        self, tenant: TenantContext, applied: Any, bucket: Any, *, event_type: str | None = None
    ) -> list[BucketCount]:
        return []

    async def count_by_type(
        self, tenant: TenantContext, applied: Any, *, event_type: str | None = None
    ) -> dict[str, int]:
        counts: dict[str, int] = {}
        for (t, _), event in self.events.items():
            if t == tenant.tenant_id:
                counts[event.event_type] = counts.get(event.event_type, 0) + 1
        return counts

    async def update_projection(
        self, tenant: TenantContext, event_id: str, projection: Projection
    ) -> None:
        key = (tenant.tenant_id, event_id)
        if key in self.events:
            from dataclasses import replace

            self.events[key] = replace(self.events[key], projection=projection)

    async def find_pending_projections(self, *, stale_before: datetime, limit: int) -> list[Event]:
        from eventplatform.domain.event import ProjectionStatus

        return [
            e for e in self.events.values() if e.projection.status is not ProjectionStatus.INDEXED
        ][:limit]


class FakeSearchIndex:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.documents: dict[str, Event] = {}

    async def ensure_mapping(self) -> None: ...

    async def index(self, tenant: TenantContext, event: Event) -> None:
        if not self.available:
            from eventplatform.domain.errors import SearchUnavailable

            raise SearchUnavailable("elasticsearch is down")
        self.documents[f"{tenant.tenant_id}:{event.event_id}"] = event

    async def search(
        self, tenant: TenantContext, query: SearchQuery, *, cursor: str | None, limit: int
    ) -> SearchResults:
        hits = [
            e
            for key, e in self.documents.items()
            if key.startswith(f"{tenant.tenant_id}:")
            and query.terms.lower() in str(e.metadata).lower()
        ]
        return SearchResults(items=hits[:limit], total=len(hits), next_cursor=None)


class FakeDeadLetterStore:
    def __init__(self) -> None:
        self.letters: dict[tuple[str, str], DeadLetter] = {}

    async def record(self, tenant: TenantContext, letter: DeadLetter) -> None:
        self.letters[(tenant.tenant_id, letter.event_id)] = letter

    async def list(
        self, tenant: TenantContext, *, cursor: str | None, limit: int
    ) -> tuple[list[DeadLetter], str | None]:
        return [v for (t, _), v in self.letters.items() if t == tenant.tenant_id][:limit], None

    async def get(self, tenant: TenantContext, event_id: str) -> DeadLetter | None:
        return self.letters.get((tenant.tenant_id, event_id))

    async def mark_replayed(self, tenant: TenantContext, event_id: str, *, at: datetime) -> None:
        from dataclasses import replace

        key = (tenant.tenant_id, event_id)
        if key in self.letters:
            self.letters[key] = replace(self.letters[key], replayed_at=at)

    async def count(self, tenant: TenantContext) -> int:
        return sum(1 for (t, _) in self.letters if t == tenant.tenant_id)


class FakeStatsCache:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.store: dict[str, dict[str, Any]] = {}
        self.locks: set[str] = set()
        self.set_calls = 0

    def _check(self) -> None:
        if not self.available:
            raise CacheUnavailable("redis is down")

    async def get(self, tenant: TenantContext, key: str) -> CachedValue | None:
        self._check()
        value = self.store.get(key)
        return CachedValue(value=value, age_seconds=3) if value is not None else None

    async def set(
        self, tenant: TenantContext, key: str, value: dict[str, Any], *, ttl: int
    ) -> None:
        self._check()
        self.set_calls += 1
        self.store[key] = value

    async def acquire_lock(self, tenant: TenantContext, key: str, *, ttl: int) -> bool:
        self._check()
        if key in self.locks:
            return False
        self.locks.add(key)
        return True

    async def release_lock(self, tenant: TenantContext, key: str) -> None:
        self._check()
        self.locks.discard(key)
