"""Domain policy unit tests (quickstart map rows 10, 14, 15, 16).

These need no services: the policies are pure functions, which is the point of keeping
them in the domain rather than inside adapters.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest

from eventplatform.domain.errors import ValidationError
from eventplatform.domain.metadata import validate_metadata
from eventplatform.domain.policies.cache_key import realtime_stats_key
from eventplatform.domain.policies.idempotency import InsertOutcome, classify_duplicate
from eventplatform.domain.policies.retry import RetryPolicy
from eventplatform.domain.policies.time_window import (
    Bucket,
    TimeWindowPolicy,
    bucket_starts,
    resolve_range,
)

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
LIMITS = {"max_bytes": 16_384, "max_depth": 3, "max_keys": 50}


class TestRetryScheduleBackoffJitter:
    """map #16"""

    def test_delay_never_exceeds_the_exponential_ceiling(self) -> None:
        policy = RetryPolicy(base_seconds=1.0, cap_seconds=60.0)
        rng = random.Random(7)
        for attempt, ceiling in [(1, 1.0), (2, 2.0), (3, 4.0), (4, 8.0)]:
            for _ in range(50):
                assert 0.0 <= policy.delay_for(attempt, rng=rng) <= ceiling

    def test_delay_is_capped(self) -> None:
        policy = RetryPolicy(base_seconds=1.0, cap_seconds=60.0)
        assert policy.delay_for(20, rng=random.Random(1)) <= 60.0

    def test_jitter_actually_varies(self) -> None:
        # Without jitter every worker retries in lockstep and hammers a recovering
        # dependency at exactly the same instant.
        policy = RetryPolicy()
        rng = random.Random(3)
        assert len({policy.delay_for(5, rng=rng) for _ in range(20)}) > 1

    def test_stops_at_max_attempts(self) -> None:
        policy = RetryPolicy(max_attempts=5)
        assert policy.should_retry(4)
        assert not policy.should_retry(5)

    def test_attempt_zero_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="attempt must be"):
            RetryPolicy().delay_for(0)


class TestOccurredAtWindow:
    """map #15"""

    def test_accepts_a_recent_timestamp(self) -> None:
        policy = TimeWindowPolicy()
        assert policy.validate_occurred_at(NOW - timedelta(hours=1), now=NOW)

    def test_rejects_a_far_future_timestamp(self) -> None:
        with pytest.raises(ValidationError, match="future"):
            TimeWindowPolicy().validate_occurred_at(NOW + timedelta(hours=1), now=NOW)

    def test_tolerates_clock_skew(self) -> None:
        # A sender a couple of minutes fast is normal and must not be rejected.
        assert TimeWindowPolicy().validate_occurred_at(NOW + timedelta(minutes=2), now=NOW)

    def test_rejects_a_stale_timestamp(self) -> None:
        with pytest.raises(ValidationError, match="past"):
            TimeWindowPolicy().validate_occurred_at(NOW - timedelta(days=120), now=NOW)

    def test_requires_a_timezone(self) -> None:
        with pytest.raises(ValidationError, match="timezone"):
            TimeWindowPolicy().validate_occurred_at(datetime(2026, 8, 31, 12, 0), now=NOW)


class TestRangeBounds:
    def test_wide_range_is_clamped_and_says_so(self) -> None:
        applied = resolve_range(
            NOW - timedelta(days=400), NOW, policy=TimeWindowPolicy(max_range_days=92)
        )
        assert applied.clamped
        assert applied.end - applied.start == timedelta(days=92)

    def test_normal_range_is_untouched(self) -> None:
        applied = resolve_range(NOW - timedelta(days=7), NOW, policy=TimeWindowPolicy())
        assert not applied.clamped

    def test_inverted_range_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="after"):
            resolve_range(NOW, NOW - timedelta(days=1), policy=TimeWindowPolicy())

    def test_bucket_starts_cover_the_whole_range(self) -> None:
        applied = resolve_range(NOW - timedelta(days=3), NOW, policy=TimeWindowPolicy())
        assert len(bucket_starts(applied, Bucket.DAY)) == 4


class TestCacheKeyNeverSharedAcrossTenants:
    """map #10 - FR-028"""

    def test_same_filters_different_tenants_never_collide(self) -> None:
        filters = {"window": "1h", "event_type": "donation"}
        assert realtime_stats_key("tenant_a", filters) != realtime_stats_key("tenant_b", filters)

    def test_tenant_is_visible_in_the_key(self) -> None:
        # An operator reading Redis by hand should see who owns a key.
        assert ":tenant_a:" in realtime_stats_key("tenant_a", {"window": "1h"})

    def test_every_filter_dimension_changes_the_key(self) -> None:
        base = realtime_stats_key("t", {"window": "1h", "event_type": None})
        assert base != realtime_stats_key("t", {"window": "24h", "event_type": None})
        assert base != realtime_stats_key("t", {"window": "1h", "event_type": "click"})

    def test_key_is_order_independent(self) -> None:
        assert realtime_stats_key("t", {"a": 1, "b": 2}) == realtime_stats_key(
            "t", {"b": 2, "a": 1}
        )

    def test_absent_and_explicit_none_are_the_same_query(self) -> None:
        assert realtime_stats_key("t", {"window": "1h"}) == realtime_stats_key(
            "t", {"window": "1h", "event_type": None}
        )


class TestMetadataLimitsRejected:
    """map #14 - SC-012"""

    def test_accepts_ordinary_metadata(self) -> None:
        assert validate_metadata({"browser": "Safari"}, **LIMITS) == {"browser": "Safari"}

    def test_none_becomes_empty(self) -> None:
        assert validate_metadata(None, **LIMITS) == {}

    def test_rejects_oversized_payload(self) -> None:
        with pytest.raises(ValidationError, match="bytes"):
            validate_metadata({"blob": "x" * 20_000}, **LIMITS)

    def test_rejects_excessive_depth(self) -> None:
        with pytest.raises(ValidationError, match="deep"):
            validate_metadata({"a": {"b": {"c": {"d": 1}}}}, **LIMITS)

    def test_rejects_too_many_keys(self) -> None:
        with pytest.raises(ValidationError, match="keys"):
            validate_metadata({f"k{i}": i for i in range(60)}, **LIMITS)

    def test_rejects_unsupported_value_type(self) -> None:
        with pytest.raises(ValidationError, match="unsupported type"):
            validate_metadata({"when": {1, 2, 3}}, **LIMITS)


class TestDuplicateClassification:
    def test_matching_hash_is_a_benign_duplicate(self) -> None:
        assert classify_duplicate(incoming_hash="a", stored_hash="a") is (
            InsertOutcome.DUPLICATE_SUPPRESSED
        )

    def test_differing_hash_is_a_conflict(self) -> None:
        assert classify_duplicate(incoming_hash="a", stored_hash="b") is (
            InsertOutcome.DUPLICATE_CONFLICT
        )
