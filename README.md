# production-llm-platform

A production-grade LLM platform, built in **10 deliberate stages** — each stage
adding one layer of real production concern, documented as it goes.

> ### 🚧 Stage 3 of 10 complete — agents
>
> **What exists today:** a FastAPI service whose chat endpoint runs a real
> **LangGraph agent loop against the Anthropic API** — it reasons, calls tools,
> observes the results and answers — with **real token accounting** from the
> model, **conversation history in Postgres** behind a **Redis read-through
> cache**, and SSE streaming. Plus everything from Stages 1–2: health /
> readiness / version / metrics endpoints, structured JSON logging, typed
> layered configuration, pooled datastore connections, a six-container Docker
> Compose stack, and a strict CI quality gate.
>
> **What does not exist yet:** **no RAG or vector search** and **no
> authentication** — the API is entirely unauthenticated. Also no OpenTelemetry
> backend, no evaluation, no Kubernetes. Those components are **interface stubs
> that raise `NotImplementedError`** — deliberately, not accidentally. Qdrant is
> connected and probed but holds no data (Stage 4). See
> [docs/architecture.md](docs/architecture.md) for the current-vs-planned split.

---

## Architecture goals

| Goal | How the platform delivers it today |
|------|-----------------------------------|
| **Reproducible** | Exact `==` pins, committed `uv.lock`, `--frozen` installs — laptop, CI and image resolve identically. |
| **Observable** | JSON logs with request-id correlation; Prometheus `/metrics`; `@traced` seam ready for OpenTelemetry. |
| **Typed & tested** | mypy `strict` and ruff from commit #1, enforced in CI. The suite is **hermetic by construction** — it cannot call a paid API ([ADR 0009](docs/adr/0009-hermetic-llm-testing.md)). |
| **Stable seams** | Proven, not asserted: Stage 3 replaced the mock engine with a full agent stack behind the same `CompletionEngine` protocol — no route or wire-format change. Later stages fill contracts (`Retriever`, `AuthProvider`, …) rather than re-cutting the design. |
| **Secure by default** | No secrets in git, env-only credentials, non-root container, errors that never leak internals. |
| **Honest** | Docs label planned work as planned. Stubs raise instead of faking. |

## Stack

**Runtime:** Python 3.12 · uv · FastAPI · Pydantic v2 · pydantic-settings · uvicorn · `langgraph==1.2.9` · `anthropic==0.116.0`
**Planned (pinned, wired in later stages):** LlamaIndex · OpenTelemetry
**Data:** PostgreSQL · Redis · Qdrant
**Ops:** Docker · Docker Compose · Prometheus · Grafana · GitHub Actions
**Quality:** pytest · ruff · mypy (strict) · pre-commit

Every choice is justified in [ADR 0001](docs/adr/0001-stack-selection.md).
LangChain was evaluated for Stage 3 and **not adopted** — the agent graph calls
the Anthropic SDK directly, so nothing imports it. See
[ADR 0006](docs/adr/0006-agent-loop-and-orchestration.md).

## Setup

Requires **Python 3.12**, **uv**, **Docker Desktop** (Compose v2) and **git**.

```bash
git clone <repo> && cd production-llm-platform

uv python install 3.12
uv venv --managed-python --python 3.12
uv sync
uv run pre-commit install
```

> ⚠️ First time here? Read
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
docs/              architecture, ADRs, runbooks, stage summaries, prompts, diagrams
services/
  api/             ✅ HTTP surface — routes, schemas, the CompletionEngine seam
  agents/          ✅ Agent · ToolAgent · ToolRegistry (Stage 3)
  orchestrator/    ✅ AgentOrchestrator · LangGraph loop · LLM seam · conversations
  retrieval/       ⬜ stub — Stage 4        evaluation/    ⬜ stub — Stage 6
  monitoring/      ⬜ stub — Stage 5        security/      ⬜ stub — Stage 8
shared/            ✅ config · logging · observability · datastores · migrations · version
migrations/        ✅ forward-only raw SQL, applied on startup
config/            committed non-secret env profiles (dev/test/prod)
tests/             ✅ unit tests mirroring the source tree
examples/          runnable examples
infrastructure/    docker/ ✅   kubernetes/ ⬜ Stage 7   terraform/ ⬜ Stage 7
scripts/           developer helper scripts
.github/workflows/ CI
```

`⬜` = interface stub only: a README contract plus an ABC/Protocol that raises
`NotImplementedError`.

## Roadmap

Each stage ends with a summary document at a **fixed** filename:

| Stage | Name | Summary | Verification | Status |
|:-----:|------|---------|--------------|--------|
| 1 | Foundation | [`stage-01-foundation.md`](docs/stage-summaries/stage-01-foundation.md) | [log](docs/verification-log/stage-01-foundation.md) | ✅ complete |
| 2 | API | [`stage-02-api.md`](docs/stage-summaries/stage-02-api.md) | [log](docs/verification-log/stage-02-api.md) | ✅ complete |
| 3 | Agents | [`stage-03-agents.md`](docs/stage-summaries/stage-03-agents.md) | [log](docs/verification-log/stage-03-agents.md) | ✅ **current — complete** |
| 4 | RAG | `stage-04-rag.md` | — | ⬜ next |
| 5 | Observability | `stage-05-observability.md` | — | ⬜ planned |
| 6 | MLOps | `stage-06-mlops.md` | — | ⬜ planned |
| 7 | Kubernetes | `stage-07-kubernetes.md` | — | ⬜ planned |
| 8 | Security | `stage-08-security.md` | — | ⬜ planned |
| 9 | Reliability | `stage-09-reliability.md` | — | ⬜ planned |
| 10 | Portfolio | `stage-10-portfolio.md` | — | ⬜ planned |

Live progress: [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md)

## Documentation

| Doc | What it's for |
|-----|---------------|
| [architecture.md](docs/architecture.md) | Current state vs planned |
| [development.md](docs/development.md) | Setup, running, troubleshooting |
| [contributing.md](docs/contributing.md) | Workflow, branches, PRs |
| [coding-standards.md](docs/coding-standards.md) | Style, typing, errors, security |
| [adr/](docs/adr/README.md) | Why each decision was made |
| [runbooks/](docs/runbooks/README.md) | Operational procedures |
| [CLAUDE.md](CLAUDE.md) | Project memory / session context |

## License

MIT
