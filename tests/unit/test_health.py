"""Tests for the health/readiness/version/metrics endpoints and error handling."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from services.api.middleware import REQUEST_ID_HEADER
from services.api.routes.health import get_datastores
from shared.datastores import Datastore, DatastoreRegistry
from shared.version import get_version

from tests.fakes import FakeDatastore


def _with_datastores(client: TestClient, *stores: Datastore) -> None:
    """Install a registry built from ``stores`` as the app's datastore layer."""
    app = client.app
    assert isinstance(app, FastAPI)
    registry = DatastoreRegistry(list(stores))
    app.dependency_overrides[get_datastores] = lambda: registry


def test_health_returns_200_and_identity(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "api"
    assert body["version"] == get_version()
    assert body["environment"] == "test"


def test_health_never_probes_dependencies(client: TestClient) -> None:
    """/health is pure liveness: it must not dial a datastore, ever.

    The tripwire store fails on ping, so a /health that probes would 503.
    """
    tripwire = FakeDatastore("postgres", fails=True)
    _with_datastores(client, tripwire)

    resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert tripwire.ping_calls == 0


def test_readiness_reports_not_configured_datastores_in_the_test_profile(
    client: TestClient,
) -> None:
    """The test profile sets no datastore URLs, so nothing is dialled."""
    resp = client.get("/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["checks"] == {
        "postgres": "not_configured",
        "redis": "not_configured",
        "qdrant": "not_configured",
    }


def test_readiness_returns_200_when_every_datastore_answers(client: TestClient) -> None:
    _with_datastores(client, FakeDatastore("postgres"), FakeDatastore("redis"))

    resp = client.get("/ready")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["checks"] == {"postgres": "ok", "redis": "ok"}


def test_readiness_returns_503_when_a_datastore_is_unreachable(client: TestClient) -> None:
    _with_datastores(client, FakeDatastore("postgres"), FakeDatastore("redis", fails=True))

    resp = client.get("/ready")

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert body["checks"] == {"postgres": "ok", "redis": "unavailable"}


def test_version_endpoint(client: TestClient) -> None:
    resp = client.get("/version")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"service": "api", "version": get_version(), "environment": "test"}


def test_metrics_endpoint_exposes_prometheus(client: TestClient) -> None:
    # Generate at least one request so the counter is populated.
    client.get("/health")
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    assert "http_requests_total" in resp.text


def test_request_id_header_is_echoed(client: TestClient) -> None:
    resp = client.get("/health")
    assert REQUEST_ID_HEADER in resp.headers
    assert resp.headers[REQUEST_ID_HEADER]


def test_inbound_request_id_is_preserved(client: TestClient) -> None:
    resp = client.get("/health", headers={REQUEST_ID_HEADER: "trace-abc-123"})
    assert resp.headers[REQUEST_ID_HEADER] == "trace-abc-123"


def test_unknown_route_returns_error_envelope(client: TestClient) -> None:
    resp = client.get("/does-not-exist")
    assert resp.status_code == 404
    body = resp.json()
    assert set(body["error"].keys()) == {"type", "message", "request_id"}


def test_global_error_handler_returns_500_envelope(settings: object) -> None:
    """An unhandled exception is caught and returned as the uniform 500 envelope."""
    from services.api.app import create_app

    app: FastAPI = create_app()

    @app.get("/boom")
    async def boom() -> None:  # pragma: no cover - body never returns
        raise ValueError("kaboom")

    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/boom")
    assert resp.status_code == 500
    body = resp.json()
    assert body["error"]["type"] == "internal_error"
    # The internal message must NOT leak the original exception text.
    assert "kaboom" not in body["error"]["message"]
