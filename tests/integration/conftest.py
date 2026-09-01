"""API-level fixtures: a real app over real stores, driven through HTTP."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager  # type: ignore[import-not-found]

from eventplatform.api.main import create_app
from eventplatform.application.process_event import ProcessEvent
from eventplatform.composition import Container
from eventplatform.domain.policies.retry import RetryPolicy
from eventplatform.infrastructure.config.settings import Settings
from eventplatform.worker.consumer import Worker


@pytest.fixture
def api_settings(mongo_container: Any, redis_url: str, elasticsearch_url: str) -> Settings:
    return Settings(
        mongo_uri=mongo_container.get_connection_url(),
        mongo_database="eventplatform_api_test",
        redis_url=redis_url,
        elasticsearch_url=elasticsearch_url,
        elasticsearch_index="events-api-test",
        queue_backend="memory",
        queue_capacity=50,
        retry_max_attempts=3,
        retry_base_seconds=0.001,
        retry_cap_seconds=0.002,
        realtime_cache_ttl_seconds=2,
        rate_limit_per_minute=100_000,
    )


@pytest.fixture
async def api(api_settings: Settings) -> AsyncIterator[tuple[httpx.AsyncClient, Container, Worker]]:
    from eventplatform.composition import build_container
    from eventplatform.infrastructure.persistence.mongo.client import get_database

    container = build_container(api_settings)
    database = get_database(container.mongo_client, api_settings.mongo_database)
    for name in await database.list_collection_names():
        await database[name].drop()

    # The search index is shared state too. Clearing Mongo but not Elasticsearch leaves
    # documents from a previous test visible to the next one, which shows up as an
    # order-dependent failure that passes in isolation.
    if container.search is not None:
        await container.search._client.indices.delete(
            index=api_settings.elasticsearch_index, ignore_unavailable=True
        )
        await container.search.ensure_mapping()
    await container.cache._client.flushdb()

    app = create_app(api_settings, container=container)
    worker = Worker(
        queue=container.queue,
        process=ProcessEvent(
            repository=container.repository,
            search_index=container.search,
            metrics=container.metrics,
            clock=container.clock,
        ),
        dead_letters=container.dead_letters,
        retry_policy=RetryPolicy(max_attempts=3, base_seconds=0.001, cap_seconds=0.002),
        clock=container.clock,
        metrics=container.metrics,
    )

    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, container, worker

    await container.mongo_client.close()
    if container.search is not None:
        await container.search._client.close()
    await container.cache._client.aclose()


@pytest.fixture
async def tenant_keys(api: tuple[httpx.AsyncClient, Container, Worker]) -> dict[str, str]:
    _, container, _ = api
    return {
        "a": await container.tenants.issue("tenant_a"),
        "b": await container.tenants.issue("tenant_b"),
    }
