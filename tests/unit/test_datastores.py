"""Tests for the datastore connection layer: lifecycle, probing, configuration.

These tests never touch a real Postgres/Redis/Qdrant. They exercise the
registry contract against fakes; the live datastores are verified separately by
running the Compose stack and hitting ``/ready`` (see the stage summary).
"""

from __future__ import annotations

import asyncio
import time

import pytest
from shared.config import Settings
from shared.datastores import DatastoreRegistry

from tests.fakes import FakeDatastore as _FakeDatastore


async def test_probe_reports_ok_for_healthy_datastores() -> None:
    registry = DatastoreRegistry([_FakeDatastore("postgres"), _FakeDatastore("redis")])
    assert await registry.probe() == {"postgres": "ok", "redis": "ok"}


async def test_probe_reports_unavailable_for_a_failing_datastore() -> None:
    registry = DatastoreRegistry([_FakeDatastore("postgres"), _FakeDatastore("redis", fails=True)])
    assert await registry.probe() == {"postgres": "ok", "redis": "unavailable"}


async def test_probe_reports_not_configured_without_a_url() -> None:
    """An unconfigured datastore is never dialled and never reports ``ok``."""
    registry = DatastoreRegistry([_FakeDatastore("qdrant", configured=False)])
    assert await registry.probe() == {"qdrant": "not_configured"}


async def test_startup_only_connects_configured_datastores() -> None:
    wired = _FakeDatastore("postgres")
    absent = _FakeDatastore("qdrant", configured=False)
    registry = DatastoreRegistry([wired, absent])

    await registry.startup()

    assert wired.connect_calls == 1
    assert absent.connect_calls == 0


async def test_shutdown_closes_every_connected_datastore() -> None:
    first = _FakeDatastore("postgres")
    second = _FakeDatastore("redis")
    registry = DatastoreRegistry([first, second])

    await registry.startup()
    await registry.shutdown()

    assert first.close_calls == 1
    assert second.close_calls == 1


async def test_shutdown_closes_remaining_datastores_when_one_close_raises() -> None:
    """A failing close must not strand the other pools open."""

    class _BadCloser(_FakeDatastore):
        async def close(self) -> None:
            await super().close()
            raise RuntimeError("close failed")

    bad = _BadCloser("postgres")
    good = _FakeDatastore("redis")
    registry = DatastoreRegistry([bad, good])

    await registry.startup()
    await registry.shutdown()

    assert bad.close_calls == 1
    assert good.close_calls == 1


async def test_startup_does_not_raise_when_a_datastore_is_unreachable() -> None:
    """Startup must not crash the process — ``/ready`` reports the failure instead."""

    class _BadConnect(_FakeDatastore):
        async def connect(self) -> None:
            await super().connect()
            raise ConnectionError("cannot reach postgres")

    registry = DatastoreRegistry([_BadConnect("postgres")])

    await registry.startup()  # must not raise

    assert await registry.probe() == {"postgres": "unavailable"}


async def test_startup_connects_datastores_concurrently() -> None:
    """Startup must not serialise connect timeouts.

    Sequentially, three unreachable stores delay liveness by the *sum* of their
    connect timeouts — long enough for an orchestrator to kill the pod before it
    ever serves /health. Concurrently the cost is the slowest one.
    """
    delay = 0.1

    class _SlowConnect(_FakeDatastore):
        async def connect(self) -> None:
            await super().connect()
            await asyncio.sleep(delay)

    registry = DatastoreRegistry(
        [_SlowConnect("postgres"), _SlowConnect("redis"), _SlowConnect("qdrant")]
    )

    started = time.perf_counter()
    await registry.startup()
    elapsed = time.perf_counter() - started

    # Sequential would be >= 3 * delay; allow generous headroom for slow CI.
    assert elapsed < delay * 2.5


def test_registry_from_settings_builds_all_three_datastores() -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql://u:p@postgres:5432/db",
        redis_url="redis://redis:6379/0",
        qdrant_url="http://qdrant:6333",
    )
    registry = DatastoreRegistry.from_settings(settings)
    assert [store.name for store in registry.datastores] == ["postgres", "redis", "qdrant"]
    assert all(store.configured for store in registry.datastores)


def test_registry_from_settings_marks_missing_urls_unconfigured() -> None:
    registry = DatastoreRegistry.from_settings(Settings(_env_file=None))
    assert not any(store.configured for store in registry.datastores)


@pytest.mark.parametrize("name", ["postgres", "redis", "qdrant"])
async def test_unconfigured_datastore_ping_raises(name: str) -> None:
    """Guards against a pool-less datastore silently reporting healthy."""
    registry = DatastoreRegistry.from_settings(Settings(_env_file=None))
    store = next(s for s in registry.datastores if s.name == name)
    with pytest.raises(RuntimeError):
        await store.ping()
