"""Tests for the structured logging contract and request-id correlation."""

from __future__ import annotations

import json
import logging

from fastapi.testclient import TestClient
from services.api.app import create_app
from services.api.middleware import REQUEST_ID_HEADER
from shared.logging import JsonFormatter, get_request_id, set_request_id

# The fields every JSON log line must carry (the platform logging contract).
_CONTRACT_FIELDS = {
    "timestamp",
    "level",
    "service",
    "environment",
    "request_id",
    "message",
}


def _record(level: int = logging.INFO, msg: str = "hello") -> logging.LogRecord:
    return logging.LogRecord(
        name="test.logger",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )


def test_json_formatter_emits_contract_fields() -> None:
    set_request_id("rid-abc")
    try:
        formatter = JsonFormatter(service="api", environment="test")
        payload = json.loads(formatter.format(_record()))
    finally:
        set_request_id(None)

    assert payload.keys() >= _CONTRACT_FIELDS
    assert payload["level"] == "INFO"
    assert payload["service"] == "api"
    assert payload["environment"] == "test"
    assert payload["request_id"] == "rid-abc"
    assert payload["message"] == "hello"


def test_json_formatter_merges_extra_fields() -> None:
    formatter = JsonFormatter(service="api", environment="test")
    record = _record()
    record.duration_ms = 12.5  # what `extra={...}` produces
    payload = json.loads(formatter.format(record))
    assert payload["duration_ms"] == 12.5


def test_json_formatter_includes_exception_trace() -> None:
    formatter = JsonFormatter(service="api", environment="test")
    record = _record(level=logging.ERROR, msg="boom")
    try:
        raise ValueError("the cause")
    except ValueError as exc:
        record.exc_info = (type(exc), exc, exc.__traceback__)
    payload = json.loads(formatter.format(record))
    assert "exception" in payload
    assert "ValueError: the cause" in payload["exception"]
    assert "Traceback" in payload["exception"]


def test_json_formatter_is_single_line() -> None:
    """Log aggregators require one JSON object per line."""
    formatter = JsonFormatter(service="api", environment="test")
    assert "\n" not in formatter.format(_record())


def test_access_log_carries_the_request_id() -> None:
    """Regression: the access log itself must carry the request id.

    The id must still be bound when ``api.access`` emits ``http.request``.
    Unbinding it first silently logged ``request_id: null`` — the response
    header still looked correct, so only the formatted log reveals the bug.
    """
    app = create_app()
    formatter = JsonFormatter(service="api", environment="test")
    captured: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            # Format at emit time: the formatter reads the context var live.
            captured.append(formatter.format(record))

    handler = _Capture()
    access_logger = logging.getLogger("api.access")
    access_logger.addHandler(handler)
    access_logger.setLevel(logging.INFO)
    try:
        with TestClient(app) as client:
            client.get("/health", headers={REQUEST_ID_HEADER: "rid-in-log"})
    finally:
        access_logger.removeHandler(handler)

    access_lines = [
        payload
        for payload in (json.loads(line) for line in captured)
        if payload["message"] == "http.request"
    ]
    assert access_lines, "no http.request access log was emitted"
    assert access_lines[0]["request_id"] == "rid-in-log"
    assert access_lines[0]["status"] == 200


def test_request_id_is_bound_for_the_whole_request() -> None:
    """The id is visible to the route handler, and inbound headers are honoured."""
    app = create_app()

    @app.get("/_whoami")
    async def whoami() -> dict[str, str | None]:
        return {"request_id": get_request_id()}

    with TestClient(app) as client:
        resp = client.get("/_whoami", headers={REQUEST_ID_HEADER: "rid-propagated"})

    assert resp.json()["request_id"] == "rid-propagated"
    assert resp.headers[REQUEST_ID_HEADER] == "rid-propagated"


def test_request_id_is_unbound_after_the_request() -> None:
    """The id must not leak between requests."""
    app = create_app()
    with TestClient(app) as client:
        client.get("/health")
    assert get_request_id() is None
