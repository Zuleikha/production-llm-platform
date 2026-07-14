# CLAUDE.md — project memory

Read at the start of every session. Keep under ~150 lines. Facts only — rationale
lives in `docs/adr/`.

## Current state

**Stage 1 of 10 (foundation) — COMPLETE.** Version `0.1.0`.

A FastAPI `api` service that starts, reports health/readiness/version, exposes
Prometheus metrics, logs structured JSON with request-id correlation, and runs in
a 6-container Compose stack. **No LLM call, agent, RAG, DB connection or auth
exists yet** — those are later stages, and their folders are stubs that raise
`NotImplementedError`.

Roadmap + progress: **`docs/PROJECT_STATUS.md`** (canonical).
Stage 1 detail: `docs/stage-summaries/stage-01-foundation.md`.
Why anything is the way it is: **`docs/adr/`** (0001 stack, 0002 structure, 0003 config).

## Layout

```
services/api/      ✅ the only implemented service
services/{agents,orchestrator,retrieval,evaluation,monitoring,security}/  ⬜ stubs
shared/            config.py · logging.py · observability.py · version.py
config/environments/{dev,test,prod}.env    committed, NON-SECRET defaults
tests/unit/        mirrors source layout
docs/              architecture · adr · runbooks · stage-summaries · prompts
infrastructure/    docker ✅ · kubernetes ⬜ · terraform ⬜
```

## Conventions

- **Stubs:** future-stage folders get a `README.md` (contract + owning stage) plus
  an ABC/Protocol whose methods `raise NotImplementedError`. Never a working
  mini-version.
- **Naming:** `snake_case` modules/functions, `PascalCase` classes, `_private`.
  ADRs `NNNN-kebab-title.md`. Stage summaries have **fixed** filenames — see
  PROJECT_STATUS.
- **Config:** one `Settings` (pydantic-settings) in `shared/config.py`, read via
  `get_settings()` (`lru_cache`d), injected with `Depends(get_settings)`.
  Precedence: OS env > `.env` > `config/environments/<ENVIRONMENT>.env` >
  defaults. `ENVIRONMENT` = `dev`|`test`|`prod`. **No secrets in committed files**
  — credentials come from OS env only (a test enforces this).
- **Logging:** `get_logger(__name__)`; log **events** not sentences —
  `_logger.info("http.request", extra={...})`. One JSON object per line with
  `timestamp, level, service, environment, request_id, logger, message` (+
  `exception` trace on errors). Never log PII/secrets/tokens.
- **Tracing:** `@traced` on new application functions. **Exempt:**
  `shared/logging.py` + `shared/observability.py` (recursion), and async
  generators/lifespan.
- **Errors:** fail loud. Uniform envelope `{"error": {type, message, request_id}}`.
  500s never leak internals. Handlers registered against **Starlette's**
  `HTTPException` (its base), so unmatched-route 404s are caught.
- **Types:** mypy `strict`, everything annotated incl. tests. Narrow with
  `isinstance` rather than `# type: ignore`.
- **Deps:** exact `==` pins, `uv.lock` committed, `--frozen` installs. Future-stage
  libs live in `[project.optional-dependencies]`.
- **Tests:** `tests/unit/`, mirror source. Test contracts, not internals. Write the
  failing test first; never edit a test to make it pass. Switching profiles needs
  `monkeypatch.setenv` **+** `get_settings.cache_clear()`.

## Run / test / lint

```bash
uv sync                                        # install
uv run uvicorn services.api.app:app --reload   # API only  -> :8000
docker compose up -d --build                   # full stack
./scripts/smoke_health.sh                      # verify /health = 200

pwsh scripts/verify.ps1   # or ./scripts/verify.sh — the whole gate
uv run ruff check . --fix ; uv run ruff format .
uv run mypy               # strict
uv run pytest -v
```

Endpoints: `/health` `/ready` `/version` `/metrics` `/docs`.
Prometheus :9090 · Grafana :3000.

## Known environment quirks

This machine, not the code. Check here before assuming a bug.

- **Cross-drive uv.** The uv cache is on `C:`, the repo on `D:`. Cross-drive
  hardlinking is unreliable, so **`UV_LINK_MODE=copy` is required** — already set
  permanently as a User env var, and exported by both `scripts/verify.*`. A shell
  opened before it was set won't have it.
- **Do not use the system Python at `D:\Python\Python312`.** It is a
  stripped/embeddable build: no `venv` module, DLLs in the install root, and a
  venv made from it resolves `sys.base_prefix` wrongly and **segfaults on
  `import ctypes`** (surfacing as an access violation importing `httpx`, since
  `httpx`→`click`→`ctypes`). Use a uv-managed interpreter:
  `uv python install 3.12 && uv venv --managed-python --python 3.12`.
  The venv currently runs managed CPython **3.12.13**.
- **Lock/install mismatch.** If a package is in `uv.lock` but you get
  `ModuleNotFoundError` or a silent crash on import, diff `uv pip list` against
  `uv.lock` *before* assuming a code bug — installs can partially fail silently.
  Workaround used here: pin the package as an explicit direct dependency in
  `pyproject.toml` (done for `sniffio`, normally transitive via `anyio`).
- **Grafana port 3000 conflicts with `open-webui`.** An unrelated container on
  this machine binds host port 3000 first, so Grafana fails to start with
  "port is already allocated". Host port remapped to **3001** in
  `docker-compose.yml` (`"3001:3000"`). Check `docker ps` for port collisions
  before assuming a compose config bug.

## Deferred — do NOT build early

| Stage | Deferred |
|:--:|---|
| 2 | Chat/completion endpoints, streaming, DB/Redis wiring, `/ready` dependency probes |
| 3 | Agents (`Agent`), orchestration (`Orchestrator`, LangGraph) |
| 4 | RAG — `Retriever`/`VectorStore`, LlamaIndex ingest, Qdrant queries |
| 5 | OpenTelemetry export, Grafana dashboards, alerting (`@traced` logs only today) |
| 6 | Evaluation (`Evaluator`), MLOps pipelines |
| 7 | Kubernetes manifests, Terraform |
| 8 | Auth (`AuthProvider`), guardrails, rate limiting — **API is unauthenticated** |
| 9 | Load/chaos testing, SLOs |
| 10 | Portfolio polish |

Postgres/Redis/Qdrant **run in Compose but the app connects to none of them**.
`Settings.database_url|redis_url|qdrant_url` exist but are unused in Stage 1.

## Known issues

- Starlette 1.3.1 deprecates `httpx` for `TestClient` in favour of `httpx2` —
  emits a warning; harmless. Migrate when TestClient requires it.
- `config/environments/*.env` can drift from `.env.example`; nothing enforces it.
