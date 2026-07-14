"""Tests for configuration loading, profiles, and version integrity."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from shared.config import Settings, get_settings
from shared.version import __version__

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_test_profile_is_loaded_by_default() -> None:
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.environment == "test"
    assert settings.service_name == "api"


def test_prod_profile_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "prod")
    # Stage 2: prod refuses to load without these — see the fail-loud test below.
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@postgres:5432/db")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.environment == "prod"
    assert settings.is_production is True
    assert settings.debug is False
    assert settings.log_format == "json"
    get_settings.cache_clear()  # avoid leaking the prod cache to other tests


_DB_URL = "postgresql://u:p@postgres:5432/db"
_REDIS_URL = "redis://redis:6379/0"
_QDRANT_URL = "http://qdrant:6333"


@pytest.mark.parametrize(
    ("missing", "database_url", "redis_url", "qdrant_url"),
    [
        ("DATABASE_URL", None, _REDIS_URL, _QDRANT_URL),
        ("REDIS_URL", _DB_URL, None, _QDRANT_URL),
        ("QDRANT_URL", _DB_URL, _REDIS_URL, None),
    ],
)
def test_prod_refuses_to_start_without_every_datastore_url(
    missing: str,
    database_url: str | None,
    redis_url: str | None,
    qdrant_url: str | None,
) -> None:
    """A missing prod URL must fail loudly at boot, not read as `not_configured`.

    Otherwise /ready would return 200 for a service with no database.
    """
    with pytest.raises(ValueError, match=missing):
        Settings(
            _env_file=None,
            environment="prod",
            database_url=database_url,
            redis_url=redis_url,
            qdrant_url=qdrant_url,
        )


def test_non_prod_profiles_tolerate_absent_datastore_urls() -> None:
    """The test profile sets no URLs; that must stay a valid, hermetic config."""
    settings = Settings(_env_file=None, environment="test")
    assert settings.database_url is None


def test_os_env_overrides_profile_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("API_PORT", "9999")
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.api_port == 9999
    get_settings.cache_clear()


def test_no_secrets_are_hardcoded() -> None:
    """Datastore credentials must default to None (injected via env, never baked in)."""
    settings = Settings(_env_file=None)
    assert settings.database_url is None
    assert settings.redis_url is None
    assert settings.qdrant_url is None


def test_invalid_port_is_rejected() -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None, api_port=70000)


def test_runtime_version_matches_pyproject() -> None:
    """shared.version.__version__ must equal [project].version in pyproject.toml."""
    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == __version__
