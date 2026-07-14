# Stage 01 — Foundation

| | |
|---|---|
| **Stage** | 1 of 10 |
| **Status** | ✅ Complete |
| **Version** | `0.1.0` |
| **Date** | 2026-07-14 |

---

## 1. Objective

Create the project foundation and toolchain that all nine later stages inherit:
a running, observable, typed, containerised FastAPI service plus the contracts,
infrastructure and quality gate everything else plugs into — **without**
implementing any business logic.

Explicit non-goal: no LLM calls, agents, RAG, datastore connections or auth.
Where a folder's real content belongs to a later stage, it ships as an interface
stub that raises `NotImplementedError`, never a working mini-version.

## 2. Features implemented

| Feature | Detail |
|---------|--------|
| **FastAPI `api` service** | App factory (`create_app`), `GET /health`, `/ready`, `/version`, `/metrics`, OpenAPI at `/docs` |
| **Structured JSON logging** | One object per line: `timestamp`, `level`, `service`, `environment`, `request_id`, `logger`, `message`, `exception` trace; `extra={}` fields merged; console formatter for dev |
| **Request-id correlation** | `X-Request-ID` honoured inbound or generated; bound to a `ContextVar`; echoed on the response; present on every access log |
| **Typed configuration** | pydantic-settings `Settings`; `dev`/`test`/`prod` profiles; layered precedence; validated (`api_port` range, `Literal` enums); no hardcoded secrets |
| **Lifespan hooks** | `service.startup` / `service.shutdown` events |
| **Global error handling** | Uniform `{"error": {type, message, request_id}}`; 404/422/500; 500s never leak internals |
| **Prometheus metrics** | `http_requests_total` counter + `http_request_duration_seconds` histogram, labelled by **route template** to bound cardinality |
| **`@traced` decorator** | Sync + async, PEP 695 typed, signature-preserving; logs span enter/exit/duration/error — the seam Stage 5 extends to real OTel spans |
| **Interface stubs** | `Agent`, `Orchestrator`, `Retriever`/`VectorStore`, `Evaluator`, `SpanExporter`, `AuthProvider`/`Guardrail` |
| **Docker** | Multi-stage build, non-root user (uid 1001), container `HEALTHCHECK` |
| **Compose stack** | api + postgres + redis + qdrant + prometheus + grafana, healthchecks gating startup |
| **CI** | GitHub Actions: install → lint → format → mypy → pytest, **plus** Docker build + live `/health` = 200 |
| **Quality tooling** | ruff, mypy strict, pytest (21 tests), pre-commit, EditorConfig, .gitignore, .dockerignore |
| **Docs** | README, architecture, development, contributing, coding-standards, 3 ADRs, runbook, examples |

## 3. Repository structure created

```
production-llm-platform/
├── .github/workflows/ci.yml
├── config/environments/{dev,test,prod}.env
├── docs/
│   ├── adr/{README,0001-stack-selection,0002-repo-structure,0003-configuration-approach}.md
│   ├── runbooks/{README,local-stack}.md
│   ├── stage-summaries/stage-01-foundation.md
│   ├── prompts/stage-01-foundation.md
│   ├── architecture.md · development.md · contributing.md · coding-standards.md
│   └── PROJECT_STATUS.md
├── examples/{README.md,check_health.py}
├── infrastructure/
│   ├── docker/prometheus/prometheus.yml
│   ├── docker/grafana/provisioning/{datasources,dashboards}/*.yml
│   ├── kubernetes/README.md          ⬜ Stage 7
│   └── terraform/README.md           ⬜ Stage 7
├── scripts/{verify.ps1,verify.sh,smoke_health.sh,README.md}
├── services/
│   ├── api/                          ✅ implemented
│   │   ├── app.py · errors.py · metrics.py · middleware.py · __main__.py
│   │   └── routes/{health.py,meta.py}
│   ├── agents/ orchestrator/ retrieval/ evaluation/ monitoring/ security/   ⬜ stubs
├── shared/{config.py,logging.py,observability.py,version.py}
├── tests/{conftest.py,unit/{test_health.py,test_config.py,test_logging.py}}
├── CLAUDE.md · README.md · Dockerfile · docker-compose.yml
├── pyproject.toml · uv.lock · .env.example
└── .gitignore · .dockerignore · .editorconfig · .pre-commit-config.yaml · .python-version
```

Each stub package = `README.md` (contract + owning stage) + `base.py` (ABC/Protocol
raising `NotImplementedError`) + `__init__.py`.

## 4. Architecture decisions and rationale

Full records in `docs/adr/` — summary:

| ADR | Decision | Core rationale |
|-----|----------|----------------|
| [0001](../adr/0001-stack-selection.md) | Stack selection | Reproducibility (exact pins + committed lock + `--frozen`); ASGI for IO-bound LLM work; strict typing from day one because retrofitting types onto an agent codebase is far costlier later |
| [0002](../adr/0002-repo-structure.md) | Repository structure | Every future stage gets a pre-labelled home; **stub policy** — raising stubs, never working mini-versions, which drift and get depended on |
| [0003](../adr/0003-configuration-approach.md) | Configuration approach | One typed `Settings`; layered precedence so one image runs everywhere; secrets from OS env only, enforced by test |

Decisions worth calling out beyond the ADRs:

- **Stdlib logging + custom JSON formatter** over structlog/python-json-logger —
  the field contract is small and fixed; keeps the base image dependency-free
  while still funnelling uvicorn's logs through one formatter.
- **Future-stage libs as optional extras** — LangChain/LangGraph/LlamaIndex/OTel
  are pinned and committed but excluded from the base install and production
  image until the stage that uses them.
- **Metrics labelled by route template**, not raw path — raw paths are unbounded
  cardinality and will kill a Prometheus instance.
- **Handlers registered against Starlette's `HTTPException`**, not FastAPI's
  subclass — the router raises the base for unmatched routes, so registering the
  subclass silently misses every 404 (caught by test).
- **`@traced` exempts the logging/observability modules** — tracing the logging
  machinery recurses infinitely.

## 5. Dependencies added and why

**Base runtime** (all that Stage 1 needs to run):

| Package | Version | Why |
|---------|---------|-----|
| `fastapi` | 0.139.0 | ASGI framework; Pydantic-integrated validation; free OpenAPI |
| `uvicorn[standard]` | 0.51.0 | ASGI server |
| `pydantic` | 2.13.4 | Rust-core validation; one model layer for API + settings |
| `pydantic-settings` | 2.14.2 | Typed, validated env configuration (ADR 0003) |
| `prometheus-client` | 0.25.0 | Emits Prometheus text format for `/metrics` |
| `sniffio` | ≥1.3.1 | Normally transitive (anyio→httpx); pinned **explicitly** to work around a local install quirk — see § 9 |

**Dev group:** `pytest` 9.1.1 · `pytest-asyncio` 1.4.0 · `httpx` 0.28.1 (TestClient) ·
`ruff` 0.15.21 · `mypy` 2.3.0 · `pre-commit` 4.6.0.

**Optional extras — declared and pinned now, wired up later:**

| Extra | Packages | Stage |
|-------|----------|-------|
| `orchestration` | `langchain` 1.3.13, `langgraph` 1.2.9 | 3 |
| `retrieval` | `llama-index` 0.14.23, `qdrant-client` 1.18.0 | 4 |
| `observability` | `opentelemetry-api`/`-sdk` 1.43.0, `-instrumentation-fastapi` 0.64b0 | 5 |
| `datastores` | `redis` 8.0.1, `asyncpg` 0.31.0 | 2 |

All pinned with `==` (no ranges); `uv.lock` committed; CI/Docker install `--frozen`.

**Container images:** `python:3.12-slim-bookworm`, `postgres:16-alpine`,
`redis:7-alpine`, `qdrant/qdrant:v1.12.4`, `prom/prometheus:v2.55.0`,
`grafana/grafana:11.3.0`.

## 6. Key configuration decisions

- **`ENVIRONMENT`** (`dev` default | `test` | `prod`) selects
  `config/environments/<env>.env`.
- **Precedence:** OS env > `.env` (git-ignored) > profile file > field defaults.
  Compose demonstrates it: sets `ENVIRONMENT=dev` then overrides `LOG_FORMAT=json`.
- **Secrets:** committed files hold non-secret defaults only. `database_url`,
  `redis_url`, `qdrant_url` default to `None` and come from OS env — a test
  asserts this, so a hardcoded secret fails CI. They are **declared but unused**
  in Stage 1.
- **`get_settings()` is `lru_cache`d** — one validated instance per process;
  injected via `Depends(get_settings)` so tests can override without patching.
- **Validation at startup:** `api_port` constrained 1–65535; `environment` and
  `log_format` are `Literal`s. Bad config fails immediately, not at first use.
- **Version integrity:** `shared/version.py::__version__` must equal
  `[project].version` — enforced by a test.

## 7. Files created or modified

~60 files, all new (the repo was empty). See § 3 for the tree. Highlights:

**Application:** `shared/{config,logging,observability,version}.py`;
`services/api/{app,errors,metrics,middleware,__main__}.py`;
`services/api/routes/{health,meta}.py`.
**Stubs:** `services/{agents,orchestrator,retrieval,evaluation,monitoring,security}/{__init__,base}.py` + `README.md`.
**Tests:** `tests/conftest.py`, `tests/unit/{test_health,test_config,test_logging}.py`.
**Infra:** `Dockerfile`, `docker-compose.yml`, `infrastructure/docker/**`, `.github/workflows/ci.yml`.
**Docs:** `README.md`, `CLAUDE.md`, `docs/**` (architecture, 3 ADRs, guides, runbook, PROJECT_STATUS, this summary).
**Config/tooling:** `pyproject.toml`, `uv.lock`, `.env.example`, `config/environments/*.env`, `.gitignore`, `.dockerignore`, `.editorconfig`, `.pre-commit-config.yaml`, `.python-version`.

## 8. Validation completed

Actual command output (not a claim):

```console
$ uv sync --frozen
Checked 46 packages in 17ms

$ uv run ruff check .
All checks passed!

$ uv run ruff format --check .
32 files already formatted

$ uv run mypy
Success: no issues found in 31 source files

$ uv run pytest
21 passed, 1 warning in 0.20s
```

```console
$ docker compose ps
SERVICE      STATUS
api          Up About a minute (healthy)
grafana      Up 10 minutes
postgres     Up 18 minutes (healthy)
prometheus   Up 18 minutes
qdrant       Up 18 minutes
redis        Up 18 minutes (healthy)
```

**Verification bar — `/health` served by the running container:**

```console
$ curl -s -i http://localhost:8000/health
HTTP 200
{"status":"ok","service":"api","version":"0.1.0","environment":"dev"}
```

```console
$ curl -s localhost:8000/ready
{"status":"ready","checks":{}}
$ curl -s localhost:8000/version
{"service":"api","version":"0.1.0","environment":"dev"}
$ curl -s localhost:8000/metrics | grep http_requests_total
http_requests_total{method="GET",path="/health",status="200"} 51.0
http_requests_total{method="GET",path="/metrics",status="200"} 34.0
http_requests_total{method="GET",path="/ready",status="200"} 1.0
```

**Non-root container:**

```console
$ docker compose exec -T api id
uid=1001(app) gid=1001(app) groups=1001(app)
```

**Structured logs with request-id correlation** (inbound header honoured):

```console
$ docker compose logs api --tail 2
{"timestamp": "2026-07-14T10:34:16.702169+00:00", "level": "INFO", "service": "api", "environment": "dev", "request_id": "884d29dfed154cc5ae896ddecb719b11", "logger": "api.access", "message": "http.request", "method": "GET", "path": "/health", "status": 200, "duration_ms": 2.216}
{"timestamp": "2026-07-14T10:34:17.371706+00:00", "level": "INFO", "service": "api", "environment": "dev", "request_id": "demo-correlation-id", "logger": "api.access", "message": "http.request", "method": "GET", "path": "/health", "status": 200, "duration_ms": 0.933}
```

**Prometheus is scraping the api target:**

```console
$ curl -s localhost:9090/api/v1/targets
job=api          health=up
job=prometheus   health=up
```

### Bugs found and fixed during validation

1. **404s bypassed the error envelope.** Handlers were registered against
   FastAPI's `HTTPException`; the router raises Starlette's base class for
   unmatched routes, so every 404 returned the default body. Caught by
   `test_unknown_route_returns_error_envelope`.
2. **Access logs recorded `request_id: null`.** The middleware unbound the
   context var in `finally` *before* emitting the access log — the response
   header still looked correct, so only the formatted log revealed it.
   `test_access_log_carries_the_request_id` was confirmed to fail
   (`assert None == 'rid-in-log'`) with the bug re-introduced, and pass with the fix.
3. **Duplicate uncorrelated access line** from `uvicorn.access`; dropped to
   WARNING since our middleware already logs access with the request id.

## 9. Known limitations

- **`/ready` is not a real readiness probe.** It returns `checks: {}` because
  there are no dependencies to probe. It cannot fail (Stage 2).
- **The API is completely unauthenticated and unrate-limited** (Stage 8).
- **`@traced` does not produce real traces** — structured DEBUG logs only. No
  OTel backend, sampling or context propagation (Stage 5).
- **Grafana has no dashboards** — only the provisioned Prometheus datasource.
- **Postgres/Redis/Qdrant run but are unused.** Compose starts them; the app
  never connects.
- **Starlette 1.3.1 deprecation warning:** `TestClient` prefers `httpx2` over
  `httpx`. Harmless; migrate when required.
- **CI has never actually run** — the workflow is committed but unexercised
  until the first push to GitHub. Verified locally instead.
- **Windows-only environment validation.** Docker/CI paths were verified locally
  on Windows + WSL2; the Linux CI runner path is unproven (see above).

## 10. Technical debt

| Debt | Impact | Suggested fix |
|------|--------|---------------|
| `sniffio` pinned as an explicit direct dep to dodge a local install quirk | Misrepresents the true dependency graph | Remove once the cross-drive/uv install issue is confirmed resolved |
| `config/environments/*.env` can drift from `.env.example` | Silent config surprises | Add a test asserting the key sets match |
| No test coverage measurement or threshold | Coverage can rot unnoticed | Add `pytest-cov` + a CI floor (Stage 2) |
| No dependency update automation | Exact pins go stale | Dependabot/Renovate (Stage 6) |
| No container image vulnerability scanning | Unknown CVEs shipped | Trivy/Grype in CI (Stage 8) |
| Settings carries 3 unused credential fields | Mild dead code | Consumed in Stage 2 |
| `@traced` on every request adds two DEBUG log calls | Negligible now, noisy under load | Replaced by real OTel sampling (Stage 5) |
| Compose Grafana allows anonymous access, default `admin/admin` | Local-only, but a bad habit to copy | Never reuse for a deployed environment (Stage 7/8) |

## 11. Assumptions made

- **The 10-stage roadmap and its fixed filenames are settled** and recorded
  verbatim in `PROJECT_STATUS.md`; future prompts follow them without drift.
- `docs/architecture` in the prompt's structure means **`docs/architecture.md`**
  (a file), since the documentation section names that path explicitly.
- **The orchestrator has no dedicated stage**, so it is assigned to Stage 3+
  (agent orchestration) — it is the one service whose stage isn't named in the
  roadmap.
- A **single deployable** is correct for now; the seven service packages are
  logical boundaries, not separate images.
- `dev` is the right default profile, and **Compose is a local dev stack** — hence
  `ENVIRONMENT=dev` there, with `LOG_FORMAT=json` overridden to demonstrate
  precedence and showcase structured logs.
- **The uv-managed CPython is acceptable** as the local interpreter (the system
  Python is unusable — § Known environment quirks in `CLAUDE.md`).
- Installing `uv` (absent at start) and a managed CPython was in scope as
  prerequisite setup; both were reported, not done silently.

## 12. Items intentionally deferred

| Deferred | To stage |
|----------|----------|
| Chat/completion endpoints, streaming, API versioning, DB/Redis wiring, real `/ready` probes | **2 — API** |
| `Agent` implementation, tool use, `Orchestrator`/LangGraph graphs | **3 — Agents** |
| LlamaIndex ingestion, embeddings, Qdrant `Retriever`/`VectorStore`, citations | **4 — RAG** |
| OpenTelemetry export, `@traced` → real spans, Grafana dashboards, alerting | **5 — Observability** |
| `Evaluator`, eval datasets, LLM-as-judge, CI regression gates | **6 — MLOps** |
| Kubernetes manifests/Helm, Terraform | **7 — Kubernetes** |
| AuthN/AuthZ, guardrails, rate limiting, secret manager, image scanning | **8 — Security** |
| Load testing, chaos, SLOs, resilience patterns | **9 — Reliability** |
| Portfolio polish, demos, case study | **10 — Portfolio** |

## 13. Prerequisites for Stage 2

**All met — Stage 2 is unblocked.**

| Prerequisite | Status |
|--------------|--------|
| Running FastAPI app + factory (`create_app`) to hang routers on | ✅ |
| Typed config seam (`get_settings`, `Depends`) for connection strings | ✅ (`database_url`/`redis_url` fields already declared) |
| Structured logging + request-id correlation for new endpoints | ✅ |
| Error envelope + global handlers new routes inherit automatically | ✅ |
| Metrics + `@traced` seams that new code gets for free | ✅ |
| Postgres + Redis running with healthchecks gating api startup | ✅ (unused, ready to wire) |
| Green quality gate (ruff/mypy/pytest) + Docker health verification | ✅ |
| Test fixtures (`client`, `settings`) to extend | ✅ |

**To start Stage 2:**

1. Read `CLAUDE.md` (conventions + **Known environment quirks** — the uv/Python
   traps will waste an hour otherwise).
2. Move `redis`/`asyncpg` from the `datastores` extra into base `dependencies`.
3. Implement `/ready` dependency probes — the empty `checks: {}` is the
   designated extension point.
4. Add routers under `services/api/routes/`, include them in `create_app()`.
5. Write the failing test first; keep the gate green.
6. Add an ADR for each significant Stage 2 decision (vector of choice, streaming
   protocol, migration tool).
7. End with `docs/stage-summaries/stage-02-api.md` and update `PROJECT_STATUS.md`
   + `CLAUDE.md`.
