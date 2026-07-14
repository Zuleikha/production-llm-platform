"""Application configuration via ``pydantic-settings``.

Configuration is layered (highest precedence first):

1. Real OS environment variables (injected by Docker Compose / CI).
2. ``.env`` at the repo root (local, git-ignored overrides).
3. ``config/environments/<ENVIRONMENT>.env`` (committed, non-secret defaults).

The active profile is chosen by the ``ENVIRONMENT`` variable (``dev`` | ``test``
| ``prod``), defaulting to ``dev``. No secrets are hardcoded here — see ADR 0003.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["dev", "test", "prod"]
LogFormat = Literal["json", "console"]

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ENV_DIR = _REPO_ROOT / "config" / "environments"


class Settings(BaseSettings):
    """Typed, validated application settings.

    Field names map case-insensitively to environment variables, e.g.
    ``service_name`` <- ``SERVICE_NAME``.
    """

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Core identity ---
    environment: Environment = "dev"
    service_name: str = "api"

    # --- HTTP server ---
    api_host: str = "0.0.0.0"  # containers bind all interfaces by design
    api_port: int = Field(default=8000, ge=1, le=65535)
    debug: bool = False

    # --- Logging ---
    log_level: str = "INFO"
    log_format: LogFormat = "json"

    # --- Datastore connection strings (RESERVED for Stage 2+, unused here) ---
    # Declared to establish the pattern; the Stage 1 API does not connect to
    # any of these. Populated from the OS environment at deploy time.
    database_url: str | None = None
    redis_url: str | None = None
    qdrant_url: str | None = None

    @property
    def is_production(self) -> bool:
        """True when running under the ``prod`` profile."""
        return self.environment == "prod"


def _profile_env_files() -> list[Path]:
    """Resolve the ordered list of ``.env`` files to load for the active profile.

    Later files override earlier ones (OS environment still wins over all).
    """
    env = os.environ.get("ENVIRONMENT", "dev").strip().lower()
    files: list[Path] = []
    profile = _ENV_DIR / f"{env}.env"
    if profile.is_file():
        files.append(profile)
    root_override = _REPO_ROOT / ".env"
    if root_override.is_file():
        files.append(root_override)
    return files


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide cached ``Settings`` instance.

    Call ``get_settings.cache_clear()`` to force a reload (used by tests that
    switch profiles).
    """
    files = _profile_env_files()
    return Settings(_env_file=files or None)
