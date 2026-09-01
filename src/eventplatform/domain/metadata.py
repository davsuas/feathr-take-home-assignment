"""Metadata validation (FR-004, FR-005).

Customer metadata is schemaless by contract, which is exactly why it needs hard limits.
Without them a single client can bloat every downstream store: MongoDB documents,
Elasticsearch source, and the cached summaries computed over them.
"""

from __future__ import annotations

import json
from typing import Any

from eventplatform.domain.errors import ValidationError

ALLOWED_SCALARS = (str, int, float, bool, type(None))


def validate_metadata(
    metadata: dict[str, Any] | None,
    *,
    max_bytes: int,
    max_depth: int,
    max_keys: int,
) -> dict[str, Any]:
    """Validate metadata shape and size, returning it unchanged when it passes."""
    if metadata is None:
        return {}
    if not isinstance(metadata, dict):
        raise ValidationError("metadata must be an object", field="metadata")

    total_keys = _count_keys(metadata)
    if total_keys > max_keys:
        raise ValidationError(
            f"metadata has {total_keys} keys, limit is {max_keys}", field="metadata"
        )

    depth = _depth(metadata)
    if depth > max_depth:
        raise ValidationError(
            f"metadata nests {depth} levels deep, limit is {max_depth}", field="metadata"
        )

    _reject_unsupported_values(metadata)

    encoded = json.dumps(metadata, separators=(",", ":"), default=str).encode()
    if len(encoded) > max_bytes:
        raise ValidationError(
            f"metadata is {len(encoded)} bytes, limit is {max_bytes}", field="metadata"
        )
    return metadata


def _count_keys(value: Any) -> int:
    if isinstance(value, dict):
        return len(value) + sum(_count_keys(v) for v in value.values())
    if isinstance(value, list):
        return sum(_count_keys(v) for v in value)
    return 0


def _depth(value: Any, current: int = 1) -> int:
    if isinstance(value, dict) and value:
        return max(_depth(v, current + 1) for v in value.values())
    if isinstance(value, list) and value:
        return max(_depth(v, current) for v in value)
    return current


def _reject_unsupported_values(value: Any, path: str = "metadata") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValidationError(f"{path} keys must be strings", field="metadata")
            _reject_unsupported_values(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_unsupported_values(item, f"{path}[{index}]")
    elif not isinstance(value, ALLOWED_SCALARS):
        raise ValidationError(
            f"{path} has unsupported type {type(value).__name__}", field="metadata"
        )
