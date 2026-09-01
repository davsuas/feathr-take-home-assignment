"""Request correlation and timing middleware."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from fastapi import Request, Response

from eventplatform.infrastructure.observability.correlation import correlation_scope, new_id


async def correlation_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request_id = request.headers.get("X-Request-Id") or new_id("req")
    started = time.perf_counter()
    with correlation_scope(request_id=request_id):
        response = await call_next(request)
    response.headers["X-Request-Id"] = request_id
    container = getattr(request.app.state, "container", None)
    if container is not None:
        container.metrics.observe(
            "http_request_duration_seconds",
            time.perf_counter() - started,
            route=request.url.path,
        )
    return response
