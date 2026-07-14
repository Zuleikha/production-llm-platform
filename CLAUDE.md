# CLAUDE.md — project memory

Read at the start of every session. Keep under ~150 lines. Facts only — rationale
lives in `docs/adr/`.

## Current state

**Stage 2 of 10 (API) — COMPLETE.** Version `0.1.0`.

Stage 1's service plus: `POST /v1/chat/completions` with SSE streaming, pooled
Postgres/Redis/Qdrant connections opened in the lifespan, and a `/ready` that
does real dependency probes (503 when any is unavailable). **No LLM call is
made** — the endpoint is backed by a deterministic `EchoEngine` behind a
`CompletionEngine` protocol that Stage 3 swaps out. **No agent, RAG or auth
exists yet**; those folders are stubs that raise `NotImplementedError`.

Datastores are connected and probed but **nothing is persisted** — no schema,
migrations, cache use, or vector ops.

Roadmap + progress: **`docs/PROJECT_STATUS.md`** (canonical).
Stage detail: `docs/stage-summaries/stage-0{1-foundation,2-api}.md`.
Why anything is the way it is: **`docs/adr/`** (0001 stack, 0002 structure,
0003 config, 0004 streaming transport, 0005 datastore pooling).

## Layout

```
services/api/      ✅ the only implemented service
                   app.py · routes/{health,meta,chat}.py · schemas.py ·
                   completions.py (CompletionEngine seam) · errors.py · middleware.py
services/{agents,orchestrator,retrieval,evaluation,monitoring,security}/  ⬜ stubs
shared/            config.py · logging.py · observability.py · version.py · datastores.py
config/environments/{dev,test,prod}.env    committed, NON-SECRET defaults
tests/             a package (__init__.py); fakes.py holds shared test doubles
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
  — credentials come from OS env only (a test enforces this). **`prod` requires
  all three datastore URLs** and refuses to construct without them; the `test`
  profile ignores the root `.env` to stay hermetic.
- **Datastores:** `DatastoreRegistry.from_settings()` on `app.state.datastores`,
  read via the `get_datastores` dependency. A store with no URL is
  `not_configured` and never dialled. `startup()` is concurrent and **never
  raises** — failures surface on `/ready`, not as a crash loop. `/health` must
  **never** probe a dependency (a tripwire test enforces this).
- **Chat:** routes call the `CompletionEngine` protocol on `app.state.engine`
  (`get_engine` dependency), never `services.orchestrator` directly. Stage 2's
  `EchoEngine` is a mock; Stage 3 substitutes behind the same protocol.
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

Endpoints: `/health` `/ready` `/version` `/metrics` `/docs`
`POST /v1/chat/completions` (`"stream": true` for SSE).
Prometheus :9090 · Grafana :3001 (see quirks).

## Known environment quirks

This machine, not the code. Check here before assuming a bug.

- **Cross-drive uv / lock-install mismatch.** The uv cache is on `C:`, the repo
  on `D:`. uv's default hardlink mode fails across drives, and fails **silently**
  — the package stays in `uv.lock` but never lands in the venv, surfacing later
  as a baffling `ModuleNotFoundError`. Fixed repo-wide by `link-mode = "copy"`
  under `[tool.uv]`, so a fresh clone is safe on any drive; `UV_LINK_MODE=copy`
  (User env var + `scripts/verify.*`) is now redundant belt-and-braces. If a
  locked package still won't import, diff `uv pip list` against `uv.lock` before
  assuming a code bug. `sniffio` stays pinned as an explicit direct dep (normally
  transitive via `anyio`) from when this bit before the cause was understood.
- **Do not use the system Python at `D:\Python\Python312`.** It is a
  stripped/embeddable build: no `venv` module, DLLs in the install root, and a
  venv made from it resolves `sys.base_prefix` wrongly and **segfaults on
  `import ctypes`** (surfacing as an access violation importing `httpx`, since
  `httpx`→`click`→`ctypes`). Use a uv-managed interpreter:
  `uv python install 3.12 && uv venv --managed-python --python 3.12`.
  The venv currently runs managed CPython **3.12.13**.
- **Grafana port 3000 conflicts with `open-webui`.** An unrelated container on
  this machine binds host port 3000 first, so Grafana fails to start with
  "port is already allocated". Host port remapped to **3001** in
  `docker-compose.yml` (`"3001:3000"`). Check `docker ps` for port collisions
  before assuming a compose config bug.

## Deferred — do NOT build early

| Stage | Deferred |
|:--:|---|
| 3 | Real model calls, agents (`Agent`), orchestration (`Orchestrator`, LangGraph), DB schema/migrations, Redis caching |
| 4 | RAG — `Retriever`/`VectorStore`, LlamaIndex ingest, Qdrant queries (`qdrant-client`) |
| 5 | OpenTelemetry export, Grafana dashboards, alerting (`@traced` logs only today) |
| 6 | Evaluation (`Evaluator`), MLOps pipelines |
| 7 | Kubernetes manifests, Terraform |
| 8 | Auth (`AuthProvider`), guardrails, rate limiting — **API is unauthenticated** |
| 9 | Load/chaos testing, SLOs, pool tuning, reconnect/circuit breaking |
| 10 | Portfolio polish |

Postgres/Redis/Qdrant are **connected and probed, but nothing is persisted** — no
schema, migrations, cache reads/writes or vector ops. Qdrant is only `GET /readyz`.

## Known issues

- Starlette 1.3.1 deprecates `httpx` for `TestClient` in favour of `httpx2` —
  emits a warning; harmless. Migrate when TestClient requires it.
- `config/environments/*.env` can drift from `.env.example`; nothing enforces it.
- A datastore whose `connect` fails at boot stays `unavailable` until restart (no
  reconnect). One that connects and *later* blips does recover — the driver pools
  reconnect on the next ping.
- The 422 envelope stringifies Pydantic's raw error list into `message`.
