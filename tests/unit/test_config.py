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
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.environment == "prod"
    assert settings.is_production is True
    assert settings.debug is False
    assert settings.log_format == "json"
    get_settings.cache_clear()  # avoid leaking the prod cache to other tests


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
