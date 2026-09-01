"""MongoDB connection.

PyMongo's native ``AsyncMongoClient``, not Motor: Motor passed its deprecation date on
2026-05-14 (research.md R1).
"""

from __future__ import annotations

from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase


def create_client(uri: str) -> AsyncMongoClient[dict[str, object]]:
    return AsyncMongoClient(uri, tz_aware=True, uuidRepresentation="standard")


def get_database(
    client: AsyncMongoClient[dict[str, object]], name: str
) -> AsyncDatabase[dict[str, object]]:
    return client[name]
