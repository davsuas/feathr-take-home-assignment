"""Retry schedule (R3).

A pure function of the attempt number. Keeping it here rather than inside a queue adapter
means both adapters retry identically, and the schedule is unit-testable with no broker.
"""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 5
    base_seconds: float = 1.0
    cap_seconds: float = 60.0

    def should_retry(self, attempt: int) -> bool:
        """``attempt`` is the number of attempts already made."""
        return attempt < self.max_attempts

    def delay_for(self, attempt: int, *, rng: random.Random | None = None) -> float:
        """Exponential backoff with full jitter.

        Full jitter, not equal jitter: when many workers fail against the same recovering
        dependency, a deterministic delay makes them retry in lockstep and hammer it at
        exactly the moment it is trying to come back.
        """
        if attempt < 1:
            raise ValueError("attempt must be >= 1")
        ceiling = min(self.base_seconds * (2 ** (attempt - 1)), self.cap_seconds)
        source = rng or random
        return source.uniform(0.0, ceiling)
