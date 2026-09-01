"""Redis cache adapter.

Every driver error becomes ``CacheUnavailable`` before it crosses the port. That is what
lets the use case treat a cache outage as a latency event rather than a failure, which is
the whole promise of FR-030.
"""

from __future__ import annotations

import json
import time
from typing import Any

import redis.asyncio as redis
from redis.exceptions import RedisError

from eventplatform.domain.errors import CacheUnavailable
from eventplatform.domain.identity import TenantContext
from eventplatform.ports.stats_cache import CachedValue


class RedisStatsCache:
    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    async def get(self, tenant: TenantContext, key: str) -> CachedValue | None:
        self._assert_scoped(tenant, key)
        try:
            raw = await self._client.get(key)
        except RedisError as exc:
            raise CacheUnavailable(str(exc)) from exc
        if raw is None:
            return None
        payload = json.loads(raw)
        age = max(0, int(time.time() - payload.get("stored_at", time.time())))
        return CachedValue(value=payload.get("value", {}), age_seconds=age)

    async def set(
        self, tenant: TenantContext, key: str, value: dict[str, Any], *, ttl: int
    ) -> None:
        self._assert_scoped(tenant, key)
        try:
            await self._client.set(
                key,
                json.dumps({"value": value, "stored_at": time.time()}),
                ex=ttl,
            )
        except RedisError as exc:
            raise CacheUnavailable(str(exc)) from exc

    async def acquire_lock(self, tenant: TenantContext, key: str, *, ttl: int) -> bool:
        self._assert_scoped(tenant, key)
        try:
            return bool(await self._client.set(key, "1", nx=True, ex=ttl))
        except RedisError as exc:
            raise CacheUnavailable(str(exc)) from exc

    async def release_lock(self, tenant: TenantContext, key: str) -> None:
        self._assert_scoped(tenant, key)
        try:
            await self._client.delete(key)
        except RedisError as exc:
            raise CacheUnavailable(str(exc)) from exc

    @staticmethod
    def _assert_scoped(tenant: TenantContext, key: str) -> None:
        """Defence in depth. Keys are built by a domain policy that always includes the
        tenant, so this can only fire on a programming error - but a cache key that lost
        its tenant segment is precisely the bug that leaks one customer's numbers to
        another, and it should fail loudly rather than serve."""
        if f":{tenant.tenant_id}:" not in key:
            raise ValueError(f"cache key is not scoped to tenant {tenant.tenant_id}")
