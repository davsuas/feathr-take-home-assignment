"""Worker entrypoint."""

from __future__ import annotations

import asyncio

from eventplatform.application.process_event import ProcessEvent
from eventplatform.composition import build_container
from eventplatform.domain.policies.time_window import TimeWindowPolicy
from eventplatform.infrastructure.config.settings import get_settings
from eventplatform.infrastructure.observability.logging import configure_logging, get_logger
from eventplatform.infrastructure.persistence.mongo.client import get_database
from eventplatform.infrastructure.persistence.mongo.indexes import ensure_indexes
from eventplatform.worker.consumer import Worker
from eventplatform.worker.reconciler import ProjectionReconciler

log = get_logger(__name__)


async def amain() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    container = build_container(settings)

    connect = getattr(container.queue, "connect", None)
    if connect is not None:
        await connect()

    await ensure_indexes(get_database(container.mongo_client, settings.mongo_database))
    if container.search is not None:
        await container.search.ensure_mapping()

    worker = Worker(
        queue=container.queue,
        process=ProcessEvent(
            repository=container.repository,
            search_index=container.search,
            metrics=container.metrics,
            clock=container.clock,
        ),
        dead_letters=container.dead_letters,
        retry_policy=container.retry_policy,
        clock=container.clock,
        metrics=container.metrics,
        time_policy=TimeWindowPolicy(
            max_future_seconds=settings.occurred_at_max_future_seconds,
            max_past_days=settings.occurred_at_max_past_days,
            max_range_days=settings.max_aggregate_range_days,
        ),
    )
    if container.search is None:
        raise RuntimeError("search index must be configured for the worker to reconcile")
    reconciler = ProjectionReconciler(
        repository=container.repository,
        search_index=container.search,
        clock=container.clock,
        metrics=container.metrics,
        stale_after_seconds=settings.reconciler_stale_after_seconds,
    )

    log.info("worker_starting", queue_backend=settings.queue_backend)
    await asyncio.gather(
        worker.run_forever(),
        reconciler.run_forever(interval_seconds=settings.reconciler_interval_seconds),
    )


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
