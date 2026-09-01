"""map #21 - the committed contract must not drift from the served one.

A contract nobody checks is documentation that quietly becomes fiction. This compares
what the app actually serves against `specs/.../contracts/openapi.yaml`, which is the
contract the design phase agreed to.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from eventplatform.api.main import create_app
from eventplatform.infrastructure.config.settings import Settings

CONTRACT = (
    Path(__file__).resolve().parents[2]
    / "specs"
    / "001-distributed-event-platform"
    / "contracts"
    / "openapi.yaml"
)


@pytest.fixture(scope="module")
def served() -> dict[str, Any]:
    # No lifespan: the schema is built from the route table, not from any dependency.
    return create_app(Settings(mongo_uri="mongodb://unused:27017")).openapi()


@pytest.fixture(scope="module")
def agreed() -> dict[str, Any]:
    return yaml.safe_load(CONTRACT.read_text())


def test_every_agreed_path_is_served(served: dict[str, Any], agreed: dict[str, Any]) -> None:
    missing = set(agreed["paths"]) - set(served["paths"])
    assert not missing, f"contract promises paths the app does not serve: {sorted(missing)}"


def test_every_served_path_is_in_the_contract(
    served: dict[str, Any], agreed: dict[str, Any]
) -> None:
    undocumented = {p for p in served["paths"] if p not in agreed["paths"]}
    assert not undocumented, f"app serves undocumented paths: {sorted(undocumented)}"


def test_methods_match_per_path(served: dict[str, Any], agreed: dict[str, Any]) -> None:
    for path, spec in agreed["paths"].items():
        agreed_methods = {m for m in spec if m in {"get", "post", "put", "patch", "delete"}}
        served_methods = {
            m for m in served["paths"][path] if m in {"get", "post", "put", "patch", "delete"}
        }
        assert agreed_methods == served_methods, f"method mismatch on {path}"


def test_ingestion_returns_202_not_200(served: dict[str, Any]) -> None:
    """The single most important thing this API says about itself: accepted, not stored."""
    responses = served["paths"]["/events"]["post"]["responses"]
    assert "202" in responses
    assert "200" not in responses


def test_consistency_semantics_are_stated_in_the_schema(served: dict[str, Any]) -> None:
    description = served["info"]["description"]
    assert "202" in description
    assert "does NOT mean stored" in description or "NOT mean stored" in description


def test_realtime_response_exposes_staleness(served: dict[str, Any]) -> None:
    schema = served["components"]["schemas"]["RealtimeOut"]["properties"]
    # A caller cannot judge a cached number without knowing it is cached and how old.
    assert {"cached", "age_seconds", "degraded"} <= set(schema)
