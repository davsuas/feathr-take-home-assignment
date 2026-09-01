"""Test fixtures.

Integration tests start real MongoDB, Elasticsearch, and Redis via Testcontainers, but
drive the **in-process** queue adapter. That combination is deliberate: the stores are
where the interesting behaviour lives (unique indexes, mappings, TTLs), while the queue's
semantics are identical across adapters - so the suite needs no broker and runs with one
command (constitution X.3).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest

pytest_plugins: list[str] = []


def _docker_available() -> bool:
    import shutil
    import subprocess

    docker = shutil.which("docker")
    if docker is None:
        return False
    try:
        return (
            subprocess.run(  # noqa: S603
                [docker, "info"], capture_output=True, timeout=15, check=False
            ).returncode
            == 0
        )
    except Exception:
        return False


DOCKER = _docker_available()
requires_docker = pytest.mark.skipif(not DOCKER, reason="docker is not available")


@pytest.fixture(scope="session")
def event_loop() -> Iterator[asyncio.AbstractEventLoop]:
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def mongo_container() -> Iterator[Any]:
    if not DOCKER:
        pytest.skip("docker is not available")
    from testcontainers.community.mongodb import MongoDbContainer

    # Standalone, matching production compose: this design uses no transactions and no
    # change streams, so a replica set would be ceremony with no behaviour behind it.
    #
    # Pinned to 8.2, not 8.0: MongoDB 8.0 refuses to start on Linux kernel 6.19+, which
    # is what current Docker Desktop VMs run (SERVER-121912).
    with MongoDbContainer("mongo:8.2") as container:
        yield container


@pytest.fixture(scope="session")
def redis_container() -> Iterator[Any]:
    if not DOCKER:
        pytest.skip("docker is not available")
    from testcontainers.community.redis import RedisContainer

    with RedisContainer("redis:8.0-alpine") as container:
        yield container


@pytest.fixture(scope="session")
def elasticsearch_container() -> Iterator[Any]:
    if not DOCKER:
        pytest.skip("docker is not available")
    from testcontainers.community.elasticsearch import ElasticSearchContainer

    container = (
        ElasticSearchContainer("docker.elastic.co/elasticsearch/elasticsearch:9.1.2")
        .with_env("discovery.type", "single-node")
        .with_env("xpack.security.enabled", "false")
        # Xms must equal Xmx, as in docker-compose.yml, or the JVM fights the cgroup.
        .with_env("ES_JAVA_OPTS", "-Xms512m -Xmx512m")
    )
    with container as running:
        yield running


@pytest.fixture
def elasticsearch_url(elasticsearch_container: Any) -> str:
    """The container class exposes no URL helper, so build it from host and port."""
    host = elasticsearch_container.get_container_host_ip()
    port = elasticsearch_container.get_exposed_port(9200)
    return f"http://{host}:{port}"


@pytest.fixture
def redis_url(redis_container: Any) -> str:
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    return f"redis://{host}:{port}/0"


@pytest.fixture
async def mongo_database(mongo_container: Any) -> AsyncIterator[Any]:
    from eventplatform.infrastructure.persistence.mongo.client import create_client, get_database
    from eventplatform.infrastructure.persistence.mongo.indexes import ensure_indexes

    client = create_client(mongo_container.get_connection_url())
    database = get_database(client, "eventplatform_test")
    for name in await database.list_collection_names():
        await database[name].drop()
    await ensure_indexes(database)
    yield database
    await client.close()
