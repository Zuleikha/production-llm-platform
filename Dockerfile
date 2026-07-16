# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# Multi-stage build for the `api` service. See ADR 0001.
#
# Stage 1 (builder): resolve + install dependencies into a self-contained .venv
#                    using uv and the committed uv.lock (reproducible).
# Stage 2 (runtime): copy only the venv + source into a slim image, run as a
#                    non-root user. No uv, no build tools, no dev dependencies.
# ---------------------------------------------------------------------------

# --- Builder -------------------------------------------------------------
FROM python:3.12-slim-bookworm AS builder

# Pinned uv (matches the version used locally / in CI).
COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Install dependencies first, as their own layer, so code edits don't bust the
# dependency cache. --frozen: fail if uv.lock is out of date with pyproject.toml.
# --no-dev: production image excludes pytest/ruff/mypy/pre-commit.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# --- Runtime -------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

# Non-root user (see ADR 0001: containers must not run as root).
RUN groupadd --gid 1001 app \
    && useradd --uid 1001 --gid app --create-home --shell /usr/sbin/nologin app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    ENVIRONMENT=prod

WORKDIR /app

# Virtualenv from the builder — owned by the non-root user.
COPY --from=builder --chown=app:app /app/.venv /app/.venv

# Application source only (see .dockerignore for what is excluded).
COPY --chown=app:app shared/ /app/shared/
COPY --chown=app:app services/ /app/services/
COPY --chown=app:app config/ /app/config/
# The RAG corpus and the ingestion CLI (Stage 4). The API itself never reads
# these — ingestion is an operator action with a bill attached, not a startup
# hook — but shipping them means `docker exec ... python scripts/ingest.py` can
# populate Qdrant from the same image that serves the traffic.
COPY --chown=app:app data/ /app/data/
COPY --chown=app:app scripts/ingest.py /app/scripts/ingest.py

USER app

EXPOSE 8000

# Liveness from inside the container. Uses stdlib urllib (no curl in slim image).
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=2).status == 200 else 1)"

CMD ["uvicorn", "services.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
