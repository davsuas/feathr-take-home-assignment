"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from eventplatform.api.dependencies import Unauthenticated, unauthenticated_handler
from eventplatform.api.errors import domain_error_handler
from eventplatform.api.middleware.correlation import correlation_middleware
from eventplatform.api.middleware.rate_limit import TokenBucketLimiter, build_rate_limit_middleware
from eventplatform.api.routes import dead_letters, events, health, search, stats
from eventplatform.composition import Container, build_container
from eventplatform.domain.errors import DomainError
from eventplatform.infrastructure.config.settings import Settings, get_settings
from eventplatform.infrastructure.observability.logging import configure_logging, get_logger
from eventplatform.infrastructure.persistence.mongo.client import get_database
from eventplatform.infrastructure.persistence.mongo.indexes import ensure_indexes

DESCRIPTION = """
Tenant-scoped event ingestion, querying, aggregation, search, and cached live summaries.

Consistency semantics, stated once and honoured everywhere:

- `202 Accepted` means validated and queued. It does NOT mean stored, searchable, or
  reflected in any summary.
- An event becomes retrievable via `/events` before it becomes findable via `/events/search`.
- `/events/stats/realtime` may be stale by up to the configured freshness window.
"""


def create_app(settings: Settings | None = None, *, container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    log = get_logger(__name__)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        built = container or build_container(settings)
        app.state.container = built
        # Connect the broker at startup rather than on first publish, so a broker
        # misconfiguration surfaces here instead of as a failed ingestion later.
        connect = getattr(built.queue, "connect", None)
        if connect is not None:
            await connect()
        try:
            await ensure_indexes(get_database(built.mongo_client, settings.mongo_database))
        except Exception as exc:
            # Startup continues: /readyz reports the failure, which is more useful than a
            # crash loop that hides which dependency is actually down.
            log.warning("index_creation_failed", error=type(exc).__name__)
        if built.search is not None:
            try:
                await built.search.ensure_mapping()
            except Exception as exc:
                log.warning("search_mapping_failed", error=type(exc).__name__)
        yield
        await built.queue.close()

    app = FastAPI(
        title="Multi-Tenant Event Processing Platform",
        version="1.0.0",
        description=DESCRIPTION,
        lifespan=lifespan,
    )

    app.middleware("http")(correlation_middleware)
    app.middleware("http")(
        build_rate_limit_middleware(TokenBucketLimiter(per_minute=settings.rate_limit_per_minute))
    )

    app.add_exception_handler(DomainError, domain_error_handler)
    app.add_exception_handler(Unauthenticated, unauthenticated_handler)

    for module in (events, stats, search, dead_letters, health):
        app.include_router(module.router)

    return app


def app_factory() -> Any:
    return create_app()
