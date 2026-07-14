# ADR 0001 — Stack selection

- **Status:** Accepted
- **Date:** 2026-07-14
- **Stage:** 1 (foundation)

## Context

We are building a production-grade LLM platform over 10 stages. Stage 1 must fix
the toolchain and dependency set that all later stages inherit. Choices made now
are expensive to reverse: they shape CI, the container image, typing discipline,
and how agents/RAG are eventually built. The platform must be reproducible on a
developer laptop and in CI, and must not silently drift as upstream releases move.

## Decision

### Language & runtime

| Choice | Why |
|--------|-----|
| **Python 3.12** | Required floor for the LLM ecosystem; gives PEP 695 type parameters (used by `@traced`) and `dt.UTC`. Capped at `<3.13` so a host upgrade cannot silently change the runtime. |
| **uv** | Single fast tool for resolve + lock + venv + run. Produces a committed `uv.lock` for byte-identical installs locally, in CI, and in Docker. Replaces pip + pip-tools + venv. |

### Application

| Choice | Why |
|--------|-----|
| **FastAPI** | ASGI-native (LLM workloads are IO-bound and streaming-heavy), Pydantic-integrated validation, OpenAPI for free. |
| **Pydantic v2** | Rust-core validation; one model layer serves API schemas *and* settings. |
| **pydantic-settings** | Typed, validated config from env vars — see ADR 0003. |
| **prometheus-client** | Emits the Prometheus text format directly; no exporter sidecar needed for `/metrics`. |
| **stdlib `logging` + a custom JSON formatter** | Rejected `structlog`/`python-json-logger`: the required field contract is small and fixed, and stdlib keeps the base image dependency-free while still capturing library logs (uvicorn) through one formatter. |

### Deferred-stage libraries (declared now, wired later)

`langchain` + `langgraph` (Stage 3), `llama-index` + `qdrant-client` (Stage 4),
`opentelemetry-*` (Stage 5), `redis` + `asyncpg` (Stage 2+) are pinned in
`[project.optional-dependencies]` rather than base dependencies. They are part of
the committed stack and resolve against the lock, but do not bloat the base
install or the production image until the stage that needs them.

### Quality tooling

| Choice | Why |
|--------|-----|
| **ruff** (lint + format) | One fast tool replacing flake8 + isort + black; removes formatter/linter disagreement. |
| **mypy `strict`** | Strict from day one. Retrofitting types onto an agent/RAG codebase later is far more expensive than paying the cost now. |
| **pytest** | Fixture model suits an app factory + TestClient. |
| **pre-commit** | Moves the CI gate to commit time; hook versions pinned to the `pyproject.toml` tool versions. |
| **GitHub Actions** | Native to the host; runs the identical commands to `scripts/verify.*`. |

### Infrastructure

| Choice | Why |
|--------|-----|
| **PostgreSQL** | Relational store for app state/metadata (Stage 2+). |
| **Redis** | Cache / rate limiting / queues (Stage 2+). |
| **Qdrant** | Vector store for RAG (Stage 4); runs locally in Docker, no managed service required. |
| **Prometheus + Grafana** | Metrics + dashboards; scrapes the `/metrics` the app already exposes. |
| **Docker multi-stage, non-root** | Builder installs deps with uv; runtime carries only the venv + source. Non-root `app` user (uid 1001) — a container escape should not land as root. |

### Version pinning

**Every** Python dependency is pinned with `==` (no ranges, no floating latest),
and `uv.lock` is committed. CI and Docker install with `--frozen`, which *fails*
if the lock and `pyproject.toml` disagree rather than silently re-resolving.

Exception: `sniffio>=1.3.1` is a workaround for a local install quirk — see
"Known environment quirks" in `CLAUDE.md`.

## Consequences

**Positive**
- A given commit resolves to identical dependencies everywhere, for years.
- Strict typing and lint are enforced before code exists to violate them.
- The production image contains no dev tooling and no unused LLM libraries.
- Later stages add libraries by moving an extra into base deps — not by re-choosing.

**Negative / accepted trade-offs**
- Exact pins mean upgrades are a deliberate, scheduled chore (mitigated later with Dependabot/Renovate).
- mypy strict costs annotation effort on every new function.
- Optional extras mean a developer working on Stage 3 must `uv sync --extra orchestration`.
- uv is younger than pip; mitigated by the committed lock and pinned uv version.
- Six containers is a heavier laptop footprint than the Stage 1 app strictly needs, accepted so infra is ready and reproducible from the start.
