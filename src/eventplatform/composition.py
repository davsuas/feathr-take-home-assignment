"""Composition root.

Wires adapters to ports once, at startup. This is the only module that knows which
concrete adapter is mounted - which is what lets the SQS migration in ADR-007 be an
adapter swap rather than a rewrite.

It lives outside both ``api`` and ``worker`` because both compose the same graph. Putting
it in ``api`` forced the worker to import the web package to build itself, which the
architecture boundary test correctly flagged as coupling.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import redis.asyncio as redis
from elasticsearch import AsyncElasticsearch

from eventplatform.application.aggregate_stats import AggregateStats
from eventplatform.application.ingest_event import IngestEvent
from eventplatform.application.query_events import QueryEvents
from eventplatform.application.realtime_stats import RealtimeStats
from eventplatform.application.replay_dead_letter import ReplayDeadLetter
from eventplatform.application.search_events import SearchEvents
from eventplatform.domain.policies.retry import RetryPolicy
from eventplatform.domain.policies.time_window import TimeWindowPolicy
from eventplatform.infrastructure.config.settings import Settings
from eventplatform.infrastructure.observability.correlation import new_id
from eventplatform.infrastructure.observability.metrics import PrometheusMetrics
from eventplatform.infrastructure.persistence.mongo.client import create_client, get_database
from eventplatform.infrastructure.persistence.mongo.dead_letter_store import MongoDeadLetterStore
from eventplatform.infrastructure.persistence.mongo.event_repository import MongoEventRepository
from eventplatform.infrastructure.persistence.mongo.tenant_directory import MongoTenantDirectory
from eventplatform.infrastructure.queue.in_memory import InMemoryQueue
from eventplatform.infrastructure.search.elasticsearch.index import ElasticsearchEventIndex
from eventplatform.ports.clock import SystemClock


@dataclass
class Container:
    """Wires adapters to ports once, at startup. The only place in the codebase that
    knows which concrete adapter is mounted."""

    settings: Settings
    metrics: PrometheusMetrics
    clock: SystemClock
    mongo_client: Any
    repository: MongoEventRepository
    dead_letters: MongoDeadLetterStore
    tenants: MongoTenantDirectory
    queue: Any
    search: ElasticsearchEventIndex | None
    cache: Any
    ingest: IngestEvent
    query: QueryEvents
    stats: AggregateStats
    realtime: RealtimeStats
    search_events: SearchEvents | None
    replay: ReplayDeadLetter
    retry_policy: RetryPolicy


def _build_queue(settings: Settings) -> Any:
    """Select the queue adapter from configuration.

    This is the whole point of the port: the domain never learns which one is mounted, and
    swapping RabbitMQ for SQS is one more branch here plus one more file in
    infrastructure/queue/ (ADR-007).
    """
    if settings.queue_backend == "rabbitmq":
        from eventplatform.infrastructure.queue.rabbitmq import RabbitMQQueue

        return RabbitMQQueue(settings.rabbitmq_url)
    return InMemoryQueue(
        capacity=settings.queue_capacity,
        visibility_timeout=settings.queue_visibility_timeout_seconds,
    )


def build_container(settings: Settings, *, queue: Any | None = None) -> Container:
    from eventplatform.infrastructure.cache.redis.stats_cache import RedisStatsCache

    metrics = PrometheusMetrics()
    clock = SystemClock()
    mongo_client = create_client(settings.mongo_uri)
    database = get_database(mongo_client, settings.mongo_database)

    repository = MongoEventRepository(database)
    dead_letters = MongoDeadLetterStore(database)
    tenants = MongoTenantDirectory(database)

    queue = queue if queue is not None else _build_queue(settings)
    search = ElasticsearchEventIndex(
        AsyncElasticsearch(settings.elasticsearch_url), settings.elasticsearch_index
    )
    cache = RedisStatsCache(redis.from_url(settings.redis_url, decode_responses=True))

    time_policy = TimeWindowPolicy(
        max_future_seconds=settings.occurred_at_max_future_seconds,
        max_past_days=settings.occurred_at_max_past_days,
        max_range_days=settings.max_aggregate_range_days,
    )

    return Container(
        settings=settings,
        metrics=metrics,
        clock=clock,
        mongo_client=mongo_client,
        repository=repository,
        dead_letters=dead_letters,
        tenants=tenants,
        queue=queue,
        search=search,
        cache=cache,
        ingest=IngestEvent(
            queue=queue,
            clock=clock,
            metrics=metrics,
            publish_timeout_seconds=settings.queue_publish_timeout_seconds,
            id_factory=new_id,
        ),
        query=QueryEvents(
            repository=repository, time_policy=time_policy, max_page_size=settings.max_page_size
        ),
        stats=AggregateStats(repository=repository, time_policy=time_policy),
        realtime=RealtimeStats(
            repository=repository,
            cache=cache,
            clock=clock,
            metrics=metrics,
            ttl_seconds=settings.realtime_cache_ttl_seconds,
            lock_ttl_seconds=settings.single_flight_lock_ttl_seconds,
        ),
        search_events=SearchEvents(search_index=search, max_page_size=settings.max_page_size),
        replay=ReplayDeadLetter(
            store=dead_letters,
            queue=queue,
            clock=clock,
            publish_timeout_seconds=settings.queue_publish_timeout_seconds,
            id_factory=new_id,
        ),
        retry_policy=RetryPolicy(
            max_attempts=settings.retry_max_attempts,
            base_seconds=settings.retry_base_seconds,
            cap_seconds=settings.retry_cap_seconds,
        ),
    )
