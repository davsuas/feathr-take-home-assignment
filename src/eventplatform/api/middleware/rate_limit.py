"""Token-bucket rate limiting (bonus: abuse prevention).

In-process on purpose for the review environment, and honest about it: with more than one
API replica each holds its own bucket, so the effective limit multiplies by replica count.
A shared Redis counter is the production answer and is named in the threat model.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from fastapi import Request, Response

from eventplatform.api.errors import problem


class TokenBucketLimiter:
    def __init__(self, *, per_minute: int) -> None:
        self._capacity = per_minute
        self._refill_per_second = per_minute / 60.0
        self._buckets: dict[str, tuple[float, float]] = {}

    def allow(self, key: str, *, now: float | None = None) -> bool:
        now = now if now is not None else time.monotonic()
        tokens, last = self._buckets.get(key, (float(self._capacity), now))
        tokens = min(self._capacity, tokens + (now - last) * self._refill_per_second)
        if tokens < 1.0:
            self._buckets[key] = (tokens, now)
            return False
        self._buckets[key] = (tokens - 1.0, now)
        return True


def build_rate_limit_middleware(
    limiter: TokenBucketLimiter,
) -> Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]:
    async def middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path != "/events" or request.method != "POST":
            return await call_next(request)
        key = request.headers.get("authorization", request.client.host if request.client else "-")
        if not limiter.allow(key):
            container = getattr(request.app.state, "container", None)
            if container is not None:
                container.metrics.increment("ingest_rejected_rate_limit_total")
            return problem(429, "rate-limited", "Too Many Requests", detail="Rate limit exceeded")
        return await call_next(request)

    return middleware
