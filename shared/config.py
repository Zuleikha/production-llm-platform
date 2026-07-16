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

from pydantic import Field, model_validator
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

    # --- Datastore connection strings ---
    # Wired up in Stage 2. Populated from the OS environment at deploy time; a
    # store left unset is reported as `not_configured` by /ready rather than
    # dialled, which keeps the test profile hermetic. Under `prod` all three are
    # mandatory — see _require_datastore_urls_in_prod below and ADR 0005.
    database_url: str | None = None
    redis_url: str | None = None
    qdrant_url: str | None = None

    # --- Datastore pooling (ADR 0005) ---
    db_pool_min_size: int = Field(default=1, ge=1)
    db_pool_max_size: int = Field(default=10, ge=1)
    redis_pool_max_connections: int = Field(default=10, ge=1)
    qdrant_pool_max_connections: int = Field(default=10, ge=1)
    datastore_connect_timeout_seconds: float = Field(default=5.0, gt=0)
    # Bounds how long /ready can block on a hung datastore.
    datastore_probe_timeout_seconds: float = Field(default=2.0, gt=0)

    # --- Anthropic / agent loop (Stage 3, ADR 0006) ---
    # The key comes from the OS environment ONLY and is never written to a
    # committed file. Under `prod` it is mandatory — see the validator below.
    # Under `test` it is ignored entirely: the test profile cannot construct a
    # real Anthropic client at all (ADR 0009).
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-4-8"
    # The Anthropic API requires max_tokens on every request; a chat request that
    # omits it falls back to this.
    anthropic_max_tokens: int = Field(default=4096, gt=0)
    anthropic_timeout_seconds: float = Field(default=60.0, gt=0)
    # Bounds the reason -> act -> observe loop so a model that keeps calling
    # tools cannot spin forever. Counts model calls, not tool calls.
    agent_max_steps: int = Field(default=6, ge=1)

    # --- Conversation cache (Stage 3, ADR 0008) ---
    conversation_cache_ttl_seconds: int = Field(default=300, gt=0)

    # --- Voyage AI / embeddings (Stage 4, ADR 0011) ---
    # Anthropic ships no embeddings API; Voyage is the documented pairing for
    # Claude RAG workloads. The key comes from the OS environment ONLY, exactly
    # like anthropic_api_key: never a committed file, mandatory under `prod`,
    # and ignored entirely under `test` — the test profile cannot construct a
    # real Voyage client at all (ADR 0011).
    voyage_api_key: str | None = None
    voyage_model: str = "voyage-3.5-lite"
    # Voyage's output dimensionality. Declared rather than discovered because it
    # is also the Qdrant collection's vector size, which is fixed at creation:
    # a mismatch between this and the live model is a boot-time error worth
    # having, not a silent recall collapse (ADR 0012).
    voyage_embedding_dimensions: int = Field(default=1024, gt=0)
    voyage_timeout_seconds: float = Field(default=30.0, gt=0)

    # --- Retrieval / Qdrant (Stage 4, ADR 0012) ---
    qdrant_collection: str = "documents"
    # Chunking. 512 tokens is well inside Voyage's context and keeps a chunk
    # small enough that a citation points at something a human can actually read.
    chunk_size_tokens: int = Field(default=512, gt=0)
    chunk_overlap_tokens: int = Field(default=64, ge=0)
    retrieval_top_k: int = Field(default=4, ge=1)

    @model_validator(mode="after")
    def _chunk_overlap_must_fit_in_chunk(self) -> Settings:
        """Reject an overlap that is not smaller than the chunk itself.

        LlamaIndex's splitter raises on this deep inside ingestion, long after
        boot; catching it here names the two settings that disagree.
        """
        if self.chunk_overlap_tokens >= self.chunk_size_tokens:
            raise ValueError(
                "CHUNK_OVERLAP_TOKENS must be smaller than CHUNK_SIZE_TOKENS "
                f"(got {self.chunk_overlap_tokens} >= {self.chunk_size_tokens})"
            )
        return self

    @property
    def is_production(self) -> bool:
        """True when running under the ``prod`` profile."""
        return self.environment == "prod"

    @property
    def is_test(self) -> bool:
        """True when running under the ``test`` profile.

        Load-bearing, not a convenience: this is what the Anthropic client
        constructor checks to make the suite hermetic by construction. See
        ADR 0009.
        """
        return self.environment == "test"

    @model_validator(mode="after")
    def _require_secrets_and_urls_in_prod(self) -> Settings:
        """Fail loudly at boot if production is missing a required setting.

        Without this, a missing or typo'd ``DATABASE_URL`` in production would
        read as ``not_configured``, and ``/ready`` would return 200 for a
        service that has no database — a silent pass that violates fail-loud.
        Stage 3 extends the same rule to ``ANTHROPIC_API_KEY``: a prod service
        whose every chat request 401s is not a service, and the honest place to
        say so is at boot, naming the variable. Stage 4 extends it again to
        ``VOYAGE_API_KEY``: without it every retrieval query 401s, and the agent
        degrades to answering ungrounded — which is worse than failing, because
        it looks like it worked. Dev and test are exempt — the test profile
        deliberately sets none of them.
        """
        if not self.is_production:
            return self
        required = (
            "database_url",
            "redis_url",
            "qdrant_url",
            "anthropic_api_key",
            "voyage_api_key",
        )
        missing = [name for name in required if not getattr(self, name)]
        if missing:
            names = ", ".join(name.upper() for name in missing)
            raise ValueError(f"the prod profile requires these settings to be set: {names}")
        return self


def _profile_env_files() -> list[Path]:
    """Resolve the ordered list of ``.env`` files to load for the active profile.

    Later files override earlier ones (OS environment still wins over all).

    The ``test`` profile deliberately ignores the repo-root ``.env``: the suite
    must produce the same result on every machine, and a developer who copied
    ``.env.example`` (which sets real datastore URLs) would otherwise have the
    tests dial a live Postgres.
    """
    env = os.environ.get("ENVIRONMENT", "dev").strip().lower()
    files: list[Path] = []
    profile = _ENV_DIR / f"{env}.env"
    if profile.is_file():
        files.append(profile)
    root_override = _REPO_ROOT / ".env"
    if env != "test" and root_override.is_file():
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
