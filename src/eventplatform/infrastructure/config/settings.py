"""Configuration, sourced from the environment only (Principle VIII.5).

Defaults here are safe local values, never secrets. A missing required value fails at
startup rather than at the first request that needs it.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"
    log_level: str = "INFO"

    mongo_uri: str = "mongodb://localhost:27017"
    mongo_database: str = "eventplatform"

    elasticsearch_url: str = "http://localhost:9200"
    elasticsearch_index: str = "events-v1"

    redis_url: str = "redis://localhost:6379/0"

    queue_backend: Literal["memory", "rabbitmq"] = "memory"
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"
    queue_capacity: int = 10_000
    queue_publish_timeout_seconds: float = 2.0
    queue_visibility_timeout_seconds: float = 30.0

    retry_max_attempts: int = 5
    retry_base_seconds: float = 1.0
    retry_cap_seconds: float = 60.0

    realtime_cache_ttl_seconds: int = 30
    single_flight_lock_ttl_seconds: int = 5

    max_page_size: int = 200
    default_page_size: int = 50
    max_aggregate_range_days: int = 92
    occurred_at_max_future_seconds: int = 300
    occurred_at_max_past_days: int = 90

    metadata_max_bytes: int = 16_384
    metadata_max_depth: int = 3
    metadata_max_keys: int = 50

    rate_limit_per_minute: int = 6_000
    reconciler_interval_seconds: float = 15.0
    reconciler_stale_after_seconds: float = 30.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
