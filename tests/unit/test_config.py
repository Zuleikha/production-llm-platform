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
    # prod refuses to load without these — see the fail-loud tests below.
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@postgres:5432/db")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    monkeypatch.setenv("VOYAGE_API_KEY", "pa-not-a-real-key")
    monkeypatch.setenv("API_KEYS", "principal:not-a-real-hash")
    monkeypatch.setenv("API_KEY_HASH_SECRET", "not-a-real-pepper")
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.environment == "prod"
    assert settings.is_production is True
    assert settings.is_test is False
    assert settings.debug is False
    assert settings.log_format == "json"
    get_settings.cache_clear()  # avoid leaking the prod cache to other tests


_DB_URL = "postgresql://u:p@postgres:5432/db"
_REDIS_URL = "redis://redis:6379/0"
_QDRANT_URL = "http://qdrant:6333"
_API_KEY = "sk-ant-not-a-real-key"
_VOYAGE_KEY = "pa-not-a-real-key"
_API_KEYS = "principal:not-a-real-hash"
_HASH_SECRET = "not-a-real-pepper"

_ALL = {
    "database_url": _DB_URL,
    "redis_url": _REDIS_URL,
    "qdrant_url": _QDRANT_URL,
    "anthropic_api_key": _API_KEY,
    "voyage_api_key": _VOYAGE_KEY,
    "api_keys": _API_KEYS,
    "api_key_hash_secret": _HASH_SECRET,
}


@pytest.mark.parametrize(
    ("missing", "field"),
    [
        ("DATABASE_URL", "database_url"),
        ("REDIS_URL", "redis_url"),
        ("QDRANT_URL", "qdrant_url"),
        # Stage 3 extends the same rule to the model credential: a prod service
        # whose every chat request 401s should not have booted (ADR 0006).
        ("ANTHROPIC_API_KEY", "anthropic_api_key"),
        # Stage 4 extends it again to the embeddings credential. Without it every
        # retrieval 401s and the agent answers ungrounded — which looks like it
        # worked, and is therefore worse than failing (ADR 0011).
        ("VOYAGE_API_KEY", "voyage_api_key"),
        # Stage 8 extends it to the API-key material: a prod service with no key
        # store rejects every authenticated request (ADR 0019).
        ("API_KEYS", "api_keys"),
        ("API_KEY_HASH_SECRET", "api_key_hash_secret"),
    ],
)
def test_prod_refuses_to_start_without_every_required_setting(missing: str, field: str) -> None:
    """A missing prod setting must fail loudly at boot, naming the variable.

    Otherwise /ready would return 200 for a service with no database — or, for
    the credentials, for a service that cannot answer a single request, cannot
    ground a single answer, or cannot authenticate one.
    """
    kwargs = {**_ALL, field: None}
    with pytest.raises(ValueError, match=missing):
        Settings(_env_file=None, environment="prod", **kwargs)  # type: ignore[arg-type]


def test_non_prod_profiles_tolerate_absent_datastore_urls() -> None:
    """The test profile sets no URLs; that must stay a valid, hermetic config."""
    settings = Settings(_env_file=None, environment="test")
    assert settings.database_url is None


def test_non_prod_profiles_tolerate_an_absent_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The test profile never needs a key — it cannot call Anthropic at all.

    `delenv` because a developer running the suite very likely *does* have
    ANTHROPIC_API_KEY exported, and OS env outranks everything (ADR 0003). That
    a key may be present is exactly why the hermetic guard keys on the profile
    rather than on the key's absence — see tests/unit/test_llm.py.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    settings = Settings(_env_file=None, environment="test")

    assert settings.anthropic_api_key is None
    assert settings.is_test is True


def test_the_default_model_is_pinned_in_config_not_scattered_in_code() -> None:
    settings = Settings(_env_file=None, environment="test")
    assert settings.anthropic_model == "claude-opus-4-8"


def test_os_env_overrides_profile_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("API_PORT", "9999")
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.api_port == 9999
    get_settings.cache_clear()


def test_no_secrets_are_hardcoded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Credentials must default to None — injected via env, never baked in.

    `delenv` for the API key because the developer running this very likely has
    one exported; the assertion is about the *default*, not about the machine.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    settings = Settings(_env_file=None)

    assert settings.database_url is None
    assert settings.redis_url is None
    assert settings.qdrant_url is None
    assert settings.anthropic_api_key is None


def test_the_committed_env_files_contain_no_api_key() -> None:
    """A secret in a committed file is a secret in git history, forever."""
    for path in sorted((_REPO_ROOT / "config" / "environments").glob("*.env")):
        body = path.read_text(encoding="utf-8")
        assert "sk-ant-" not in body, f"{path.name} looks like it contains a real API key"


def test_invalid_port_is_rejected() -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None, api_port=70000)


def test_runtime_version_matches_pyproject() -> None:
    """shared.version.__version__ must equal [project].version in pyproject.toml."""
    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == __version__
