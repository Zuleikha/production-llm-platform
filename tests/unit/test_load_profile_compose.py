"""The test-profile compose override must stay in sync with test.env (ADR 0020).

`docker-compose.test.yml` pins the api service's `API_KEYS` / `API_KEY_HASH_SECRET`
to literal values so they win over both the host shell and root `.env` — the only
form that guarantees the cost-free Locust Mode 1 container authenticates with the
fixed test-principal key and needs zero host-side setup (ADR 0020 addendum 2).

Literal values carry a drift risk: if `config/environments/test.env`'s test key
ever changes, the override would silently go stale and the container would 401
again while the (test.env-driven) pytest suite still passed. This test is that
guard. It is hermetic — it parses two committed text files and never touches
docker — so it runs in CI where the actual `docker compose up` cannot.

The *runtime* behaviour (the container really loading the test key) is a
documented manual check in `tests/load/README.md`, not pytest, because a unit test
cannot reach docker-compose.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_OVERRIDE = _REPO_ROOT / "docker-compose.test.yml"
_TEST_ENV = _REPO_ROOT / "config" / "environments" / "test.env"


def _test_env() -> dict[str, str]:
    """Parse config/environments/test.env into a dict (KEY=VALUE, skip comments)."""
    values: dict[str, str] = {}
    for line in _TEST_ENV.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def _override_api_env() -> dict[str, str | None]:
    """The api service's environment map from the test-profile compose override."""
    data = yaml.safe_load(_OVERRIDE.read_text(encoding="utf-8"))
    env = data["services"]["api"]["environment"]
    assert isinstance(env, dict), "the override must use the map form of `environment:`"
    return env


class TestTestProfileComposeOverride:
    def test_it_selects_the_test_profile(self) -> None:
        assert _override_api_env()["ENVIRONMENT"] == "test"

    def test_api_keys_match_test_env_exactly(self) -> None:
        """A drift here is what silently reintroduces the Mode 1 401 bug."""
        override = _override_api_env()
        env = _test_env()
        assert override["API_KEYS"] == env["API_KEYS"]
        assert override["API_KEY_HASH_SECRET"] == env["API_KEY_HASH_SECRET"]

    def test_provider_keys_are_pinned_empty(self) -> None:
        """No real host provider key should be baked into the load container."""
        override = _override_api_env()
        assert override["ANTHROPIC_API_KEY"] == ""
        assert override["VOYAGE_API_KEY"] == ""
