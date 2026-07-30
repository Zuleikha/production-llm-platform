# production-llm-platform

A production-grade LLM platform, built in **10 deliberate stages** — each stage
adding one layer of real production concern, documented as it goes.

> ### All 10 stages complete and independently verified
>
> The final stage added no new platform capability — it packaged the build for
> review: a scripted demo with real captured output ([docs/demo.md](docs/demo.md)),
> a case-study writeup ([docs/case-study.md](docs/case-study.md)), and a final
> docs/diagram pass ([verification log](docs/verification-log/stage-10-portfolio.md)).

**What exists**

- FastAPI service running a real **LangGraph agent loop against the Anthropic API**
- **RAG** grounding — Voyage embeddings → Qdrant, cited via `citations` ([ADR 0013](docs/adr/0013-citation-shape.md)/[0014](docs/adr/0014-prompt-injection-mitigation.md))
- **Observability** — OTel → Tempo, Grafana dashboards + alerts ([ADR 0016](docs/adr/0016-observability-stack.md))
- **RAG eval CI gate** ([ADR 0017](docs/adr/0017-rag-evaluation-and-regression-gate.md))
- **Kubernetes deployment** — Helm + validated Terraform ([ADR 0018](docs/adr/0018-kubernetes-and-terraform.md))
- **API-key auth, rate limiting, guardrails** ([ADR 0019](docs/adr/0019-api-authentication-rate-limiting-and-guardrails.md))
- **Reliability hardening** — circuit breaker, prompt caching, context windowing, SLO alerts, chaos-tested under load ([ADR 0020](docs/adr/0020-reliability-load-chaos-resilience-slos.md))
- Conversation history (Postgres + Redis cache), SSE streaming, strict CI quality gate

**What doesn't exist**

- JWT/OAuth, key rotation
- Per-source RAG trust tiers
- Per-conversation concurrency control
- LLM-based summarization (windowing was chosen instead)
- Voyage circuit breaking (only Anthropic is wrapped)

See [docs/architecture.md](docs/architecture.md) for the full current-vs-planned split.

---

## Architecture goals

| Goal | How the platform delivers it today |
|------|-----------------------------------|
| **Reproducible** | Exact `==` pins, committed `uv.lock`, `--frozen` installs — laptop, CI and image resolve identically. |
| **Observable** | JSON logs with request-id correlation; Prometheus `/metrics`; `@traced` emits real **OpenTelemetry spans** → Collector → Grafana Tempo ([ADR 0016](docs/adr/0016-observability-stack.md)). |
| **Typed & tested** | mypy `strict` and ruff from commit #1, enforced in CI. The suite is **hermetic by construction** — it cannot call a paid API ([ADR 0009](docs/adr/0009-hermetic-llm-testing.md)). |
| **Stable seams** | Proven repeatedly, not asserted. The mock engine was replaced with a full agent stack behind the same `CompletionEngine` protocol; retrieval arrived as one more `Tool`; `@traced` gained a real OTel backend without touching a call site; auth, rate limiting and guardrails filled the `AuthProvider`/`Guardrail` contracts as FastAPI dependencies around the same route; `CircuitBreakingLLMClient` wrapped the same `LLMClient` protocol as a drop-in for `AgentGraph`. None changed a route, a call site or the SSE format. |
| **Secure by default** | No secrets in git, env-only credentials, non-root container, errors that never leak internals. |
| **Honest** | Docs label planned work as planned. Stubs raise instead of faking. |

## Stack

Versions are pinned in `pyproject.toml` / `uv.lock` — this is the technology list.

**Runtime:** Python 3.12 · uv · FastAPI · Pydantic v2 · pydantic-settings · uvicorn · LangGraph · Anthropic SDK
**Retrieval:** llama-index-core · voyageai · qdrant-client
**Observability:** OpenTelemetry API/SDK · OTLP/HTTP exporter · OTel Collector · Grafana Tempo
**Data:** PostgreSQL · Redis · Qdrant (holds document vectors)
**Ops:** Docker · Docker Compose · Prometheus · Grafana · GitHub Actions · Kubernetes · Helm · Terraform (AWS, validate-only)
**Quality:** pytest · ruff · mypy (strict) · pre-commit · RAG eval regression gate

Every choice is justified in [ADR 0001](docs/adr/0001-stack-selection.md). Two
libraries were evaluated and **narrowed** rather than adopted wholesale: LangChain
was rejected — the agent graph calls the Anthropic SDK directly, so nothing imports
it ([ADR 0006](docs/adr/0006-agent-loop-and-orchestration.md)); and `llama-index-core`
was taken **instead of** the `llama-index` meta-package, which would have pulled a
rival vendor's SDK into the base image to run an Anthropic + Voyage stack
([ADR 0011](docs/adr/0011-embeddings-provider.md)).

## Setup

Requires **Python 3.12**, **uv**, **Docker Desktop** (Compose v2) and **git**.

```bash
git clone <repo> && cd production-llm-platform

uv python install 3.12
uv venv --managed-python --python 3.12
uv sync
uv run pre-commit install
```

> First time here? Read
> [Known environment quirks](CLAUDE.md#known-environment-quirks) — cross-drive
> installs are handled repo-wide by `link-mode = "copy"` in `pyproject.toml`, so
> a fresh clone is safe on any drive. Do not remove that setting.

## Running locally

```bash
uv run uvicorn services.api.app:app --reload     # just the API
```

```bash
docker compose up -d --build                      # the full stack
./scripts/smoke_health.sh                         # verify /health = 200
```

| Service | URL |
|---------|-----|
| api | http://localhost:8000 — `/health` `/ready` `/version` `/metrics` `/docs` · `POST /v1/chat/completions` |
| prometheus | http://localhost:9090 |
| grafana | http://localhost:3001 (host 3000 collides with an unrelated container — see [quirks](CLAUDE.md#known-environment-quirks)) |

```console
$ curl -s localhost:8000/health
{"status":"ok","service":"api","version":"0.1.0","environment":"dev"}
```

## Testing

```bash
pwsh scripts/verify.ps1     # full gate: sync → ruff → format → mypy → pytest
./scripts/verify.sh         # bash equivalent
uv run pytest -v            # tests only
```

## Repository structure

```
docs/              architecture, ADRs, runbooks, stage summaries, prompts, diagrams,
                   case-study.md · demo.md
services/
  api/             HTTP surface — routes, schemas, the CompletionEngine seam
  agents/          Agent · ToolAgent · ToolRegistry
  orchestrator/    AgentOrchestrator · LangGraph loop · LLM seam · conversations
  retrieval/       embeddings seam · Qdrant store · ingest · retriever · document_search tool
  monitoring/      tracing seam — OTLP/Local providers, OTel span export
  evaluation/      RetrievalEvaluator · metrics · dataset · baseline · LLM-judge
  security/        ApiKeyAuthProvider · RedisRateLimiter · input/excerpt/egress guardrails
shared/            config · logging · observability · datastores · migrations · version ·
                   resilience (circuit breaker) · metrics (x-cutting counters)
data/corpus/       the RAG corpus — ingested by scripts/ingest.py
data/eval/         eval dataset + regression baseline — scored by scripts/evaluate.py
migrations/        forward-only raw SQL, applied on startup
config/            committed non-secret env profiles (dev/test/prod)
tests/             unit tests mirroring the source tree
tests/load/        opt-in Locust harness, never CI — pool tuning + chaos load
examples/          runnable examples
infrastructure/    docker/ · kubernetes/ — Helm chart, kind-verified · terraform/ — AWS,
                   validated-never-applied
scripts/           helper scripts — ingest.py (costs $ outside test) · evaluate.py (RAG eval
                   gate) · demo.sh (scripted end-to-end demo) · generate_api_key.py
.github/workflows/ CI
```

## Roadmap

All 10 stages complete. Each ends with a summary document at a **fixed** filename,
paired with its verification log:

| Stage | Summary · Verification |
|:-----:|------------------------|
| 1 Foundation | [`stage-01-foundation.md`](docs/stage-summaries/stage-01-foundation.md) · [log](docs/verification-log/stage-01-foundation.md) |
| 2 API | [`stage-02-api.md`](docs/stage-summaries/stage-02-api.md) · [log](docs/verification-log/stage-02-api.md) |
| 3 Agents | [`stage-03-agents.md`](docs/stage-summaries/stage-03-agents.md) · [log](docs/verification-log/stage-03-agents.md) |
| 4 RAG | [`stage-04-rag.md`](docs/stage-summaries/stage-04-rag.md) · [log](docs/verification-log/stage-04-rag.md) |
| 5 Observability | [`stage-05-observability.md`](docs/stage-summaries/stage-05-observability.md) · [log](docs/verification-log/stage-05-observability.md) |
| 6 MLOps | [`stage-06-mlops.md`](docs/stage-summaries/stage-06-mlops.md) · [log](docs/verification-log/stage-06-mlops.md) |
| 7 Kubernetes | [`stage-07-kubernetes.md`](docs/stage-summaries/stage-07-kubernetes.md) · [log](docs/verification-log/stage-07-kubernetes.md) |
| 8 Security | [`stage-08-security.md`](docs/stage-summaries/stage-08-security.md) · [log](docs/verification-log/stage-08-security.md) |
| 9 Reliability | [`stage-09-reliability.md`](docs/stage-summaries/stage-09-reliability.md) · [log](docs/verification-log/stage-09-reliability.md) |
| 10 Portfolio | [`stage-10-portfolio.md`](docs/stage-summaries/stage-10-portfolio.md) · [log](docs/verification-log/stage-10-portfolio.md) |

Live progress: [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md)

## Documentation

| Doc | What it's for |
|-----|---------------|
| [case-study.md](docs/case-study.md) | The build, told through the decision trail — for a reviewer |
| [demo.md](docs/demo.md) | Scripted end-to-end demo with real captured output |
| [architecture.md](docs/architecture.md) | Current state vs planned |
| [development.md](docs/development.md) | Setup, running, troubleshooting |
| [contributing.md](docs/contributing.md) | Workflow, branches, PRs |
| [coding-standards.md](docs/coding-standards.md) | Style, typing, errors, security |
| [adr/](docs/adr/README.md) | Why each decision was made |
| [runbooks/](docs/runbooks/README.md) | Operational procedures |
| [CLAUDE.md](CLAUDE.md) | Project memory / session context |

## License

MIT
