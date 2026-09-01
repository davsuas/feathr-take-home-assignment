from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from eventplatform.domain.identity import TenantContext


@dataclass(frozen=True, slots=True)
class CachedValue:
    value: dict[str, Any]
    age_seconds: int


class StatsCache(Protocol):
    """Every method may raise ``CacheUnavailable``. Callers degrade, never fail (FR-030)."""

    async def get(self, tenant: TenantContext, key: str) -> CachedValue | None: ...
    async def set(
        self, tenant: TenantContext, key: str, value: dict[str, Any], *, ttl: int
    ) -> None: ...
    async def acquire_lock(self, tenant: TenantContext, key: str, *, ttl: int) -> bool: ...
    async def release_lock(self, tenant: TenantContext, key: str) -> None: ...
