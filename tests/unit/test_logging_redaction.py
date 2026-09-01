"""map #7 support - FR-036, FR-039.

Event metadata is customer-supplied and can carry constituent data. A log line is a copy
of that data in a system with different retention and access rules, so the redaction here
is a data-protection control, not tidiness.
"""

from __future__ import annotations

import json
from io import StringIO

import structlog

from eventplatform.infrastructure.observability.correlation import correlation_scope
from eventplatform.infrastructure.observability.logging import configure_logging


def _capture(**kwargs: object) -> dict[str, object]:
    stream = StringIO()
    configure_logging("INFO")
    structlog.configure(
        processors=structlog.get_config()["processors"],
        logger_factory=structlog.PrintLoggerFactory(file=stream),
        cache_logger_on_first_use=False,
    )
    structlog.get_logger("test").info("event_processed", **kwargs)
    return json.loads(stream.getvalue())


def test_payload_and_metadata_never_reach_the_log() -> None:
    line = _capture(
        payload={"contact_id": "contact_42", "email": "donor@example.org"},
        metadata={"browser": "Safari"},
    )
    assert line["payload"] == "[redacted]"
    assert line["metadata"] == "[redacted]"
    assert "donor@example.org" not in json.dumps(line)


def test_credentials_never_reach_the_log() -> None:
    line = _capture(authorization="Bearer fk_secret", api_key="fk_secret")
    assert "fk_secret" not in json.dumps(line)


def test_safe_operational_fields_survive() -> None:
    line = _capture(outcome="inserted", attempt=2)
    assert line["outcome"] == "inserted"
    assert line["attempt"] == 2


def test_correlation_ids_are_attached() -> None:
    with correlation_scope(request_id="req_1", tenant_id="tenant_a", event_id="evt_1"):
        line = _capture()
    assert line["request_id"] == "req_1"
    assert line["tenant_id"] == "tenant_a"
    assert line["event_id"] == "evt_1"
