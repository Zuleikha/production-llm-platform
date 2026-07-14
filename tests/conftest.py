"""Shared pytest fixtures.

The ``test`` profile is forced *before* importing the application so the
module-level app in ``services.api.app`` is built with test settings.
"""

from __future__ import annotations

import os

# Must run before the app module is imported below.
os.environ.setdefault("ENVIRONMENT", "test")

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from services.api.app import create_app
from shared.config import Settings, get_settings


@pytest.fixture
def settings() -> Settings:
    """Return freshly loaded settings for the active (test) profile."""
    get_settings.cache_clear()
    return get_settings()


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    """A TestClient bound to an app built with the test settings.

    ``raise_server_exceptions=False`` so tests can assert on the 500 envelope
    produced by the global error handler instead of the exception propagating.
    """
    app = create_app(settings)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
