# Project status

**Canonical source of truth for project progress and the stage roadmap.**
Every stage prompt references this file. Update it at the end of each stage.

| | |
|---|---|
| **Current version** | `0.1.0` |
| **Current stage** | Stage 1 — Foundation (**complete**) |
| **Overall progress** | **1 / 10 stages — 10%** |
| **Next milestone** | Stage 2 — API |
| **Last updated** | 2026-07-14 |

```
Progress  [█─────────]  1/10
```

---

## Stage summary naming convention (fixed — do not drift)

Every stage ends with a summary at exactly this path and filename. This list is
canonical; future stage prompts must use these names verbatim.

```
docs/stage-summaries/stage-01-foundation.md
docs/stage-summaries/stage-02-api.md
docs/stage-summaries/stage-03-agents.md
docs/stage-summaries/stage-04-rag.md
docs/stage-summaries/stage-05-observability.md
docs/stage-summaries/stage-06-mlops.md
docs/stage-summaries/stage-07-kubernetes.md
docs/stage-summaries/stage-08-security.md
docs/stage-summaries/stage-09-reliability.md
docs/stage-summaries/stage-10-portfolio.md
```

## Completed stages

| Stage | Name | Summary | Completed |
|:-----:|------|---------|-----------|
| 1 | Foundation | [stage-01-foundation.md](stage-summaries/stage-01-foundation.md) | 2026-07-14 |

## Remaining stages

| Stage | Name | Summary file (fixed) | Objective |
|:-----:|------|----------------------|-----------|
| 2 | API | `stage-02-api.md` | Real API surface: chat/completion endpoints, streaming, request/response schemas, datastore wiring, readiness dependency probes |
| 3 | Agents | `stage-03-agents.md` | Agent loop + tool use (LangChain/LangGraph); implement `Agent` and `Orchestrator` |
| 4 | RAG | `stage-04-rag.md` | Retrieval: LlamaIndex ingestion/chunking, embeddings, Qdrant-backed `Retriever`/`VectorStore`, grounded answers with citations |
| 5 | Observability | `stage-05-observability.md` | OpenTelemetry traces/metrics export, `@traced` → real spans, Grafana dashboards, alerting |
| 6 | MLOps | `stage-06-mlops.md` | Evaluation (`Evaluator`), eval datasets, LLM-as-judge, regression gates in CI, pipelines |
| 7 | Kubernetes | `stage-07-kubernetes.md` | K8s manifests/Helm (probes → `/health`, `/ready`), Terraform provisioning |
| 8 | Security | `stage-08-security.md` | AuthN/AuthZ, input/output guardrails, rate limiting, secret management, security scanning |
| 9 | Reliability | `stage-09-reliability.md` | Load testing, chaos/failure testing, SLOs, resilience patterns |
| 10 | Portfolio | `stage-10-portfolio.md` | Final polish, docs, demos, case study writeup |

---

## Current repository capabilities

### ✅ Works today

- **`api` service (FastAPI)** — `GET /health`, `/ready`, `/version`, `/metrics`,
  plus generated OpenAPI docs at `/docs`.
- **Structured JSON logging** — one object per line with `timestamp`, `level`,
  `service`, `environment`, `request_id`, `logger`, `message`, and an
  `exception` trace on errors.
- **Request-id correlation** — `X-Request-ID` honoured inbound or generated,
  bound to a `ContextVar`, echoed on the response, present on every access log.
- **Typed layered configuration** — pydantic-settings with `dev`/`test`/`prod`
  profiles; OS env > `.env` > profile file > defaults; no secrets committed.
- **Prometheus metrics** — request counter + latency histogram, labelled by
  route template; scraped successfully by the Prometheus container.
- **Global error handling** — uniform `{"error": {...}}` envelope; 500s never
  leak internals.
- **Lifespan hooks** — `service.startup` / `service.shutdown`.
- **Docker Compose stack** — api (multi-stage, **non-root** uid 1001), postgres,
  redis, qdrant, prometheus, grafana (datasource provisioned as code).
- **Quality gate** — ruff, ruff-format, mypy `strict`, pytest (21 tests),
  pre-commit; GitHub Actions runs all of it plus a Docker build that fails
  unless the container serves `GET /health` → 200.

### ❌ Does not exist yet

No LLM calls · no agents · no RAG/vector search · no database or cache
connections · **no authentication** · no OpenTelemetry backend · no Grafana
dashboards · no Kubernetes/Terraform · no evaluation · no load testing.

Postgres, Redis and Qdrant **run** in Compose but the application connects to
none of them — the infrastructure is staged ahead of the code that will use it.

---

## Next milestone — Stage 2 (API)

**Objective:** turn the foundation into a real API surface.

Expected scope:

1. Chat/completion endpoints with Pydantic request/response schemas.
2. Streaming responses (SSE).
3. Wire PostgreSQL + Redis (connection pools, migrations, lifespan management).
4. Extend `/ready` with genuine dependency probes (currently `checks: {}`).
5. Move `redis`/`asyncpg` from the `datastores` extra into base dependencies.
6. API versioning (`/v1`) and pagination conventions.

**Prerequisites** (all met): foundation endpoints, config/logging/tracing seams,
Compose datastores running, CI gate green. See
[stage-01-foundation.md § Prerequisites for Stage 2](stage-summaries/stage-01-foundation.md#13-prerequisites-for-stage-2).
