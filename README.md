# production-llm-platform

A production-grade LLM platform, built in **10 deliberate stages** — each stage
adding one layer of real production concern, documented as it goes.

> ### 🚧 Stage 8 of 10 complete — Security
>
> **What exists today:** a FastAPI service whose chat endpoint runs a real
> **LangGraph agent loop against the Anthropic API** — it reasons, calls tools,
> observes the results and answers — and **grounds those answers in a retrieved
> document corpus** (LlamaIndex chunking → Voyage embeddings → **Qdrant**), with a
> top-level **`citations`** field ([ADR 0013](docs/adr/0013-citation-shape.md))
> and retrieved text fenced as **untrusted** behind a per-call nonce
> ([ADR 0014](docs/adr/0014-prompt-injection-mitigation.md)). **Observability is
> real** ([ADR 0016](docs/adr/0016-observability-stack.md)): every `@traced` call
> site emits an **OpenTelemetry span** and each request is a root span, exported
> OTLP/HTTP → **OTel Collector → Grafana Tempo**, with Grafana dashboards and
> symptom alerts provisioned as code. **The RAG pipeline has an evaluation
> harness and a CI regression gate**
> ([ADR 0017](docs/adr/0017-rag-evaluation-and-regression-gate.md)):
> `scripts/evaluate.py` scores retrieval (recall@k / MRR) over a checked-in dataset
> using deterministic offline embeddings — a hermetic, **required** CI job that
> fails the build on a regression; an opt-in LLM-as-judge tier adds faithfulness /
> citation scoring but never runs in CI. **The API now deploys to Kubernetes**
> ([ADR 0018](docs/adr/0018-kubernetes-and-terraform.md)): a Helm chart
> (`infrastructure/kubernetes/helm/`) with liveness/readiness probes wired to
> `/health`/`/ready`, verified end-to-end on a local `kind` cluster; dev-mode
> in-cluster datastores vs managed ones are gated by `devDependencies.enabled`.
> AWS Terraform (`infrastructure/terraform/`) provisions VPC/EKS/RDS/ElastiCache/S3
> — **validated, never applied**. **The chat endpoint is now authenticated**
> ([ADR 0019](docs/adr/0019-api-authentication-rate-limiting-and-guardrails.md)):
> a bearer **API key** (only a salted `HMAC-SHA256` is stored; missing/bad/wrong all
> return the same `401`), a **Redis-backed per-principal rate limiter** (`429`,
> fail-open on a Redis outage), and heuristic **input/excerpt/egress guardrails** on
> top of the nonce fence — plus two hermetic CI gates, **gitleaks** (secret scan) and
> **pip-audit** (dependency scan). Plus everything from Stages 1–3: **real
> token accounting**, **conversation history in Postgres** behind a **Redis
> read-through cache**, SSE streaming, health / readiness / version / metrics
> endpoints, structured JSON logging, typed layered configuration, pooled datastore
> connections, a Docker Compose stack, and a strict CI quality gate.
>
> **What does not exist yet:** authentication is **API-key only** — no JWT/OAuth/IdP,
> no key rotation, single-tier authZ, and no per-source RAG trust tiers (all deferred
> by decision, [ADR 0019](docs/adr/0019-api-authentication-rate-limiting-and-guardrails.md)).
> No load/chaos testing or SLOs (Stage 9). Metrics are **not** exported through the
> OTel collector (traces only,
> [ADR 0016](docs/adr/0016-observability-stack.md)) — deferred to Stage 9.
> Evaluation covers **RAG retrieval only**. Remaining future-stage components are
> **interface stubs that raise `NotImplementedError`**.
> See [docs/architecture.md](docs/architecture.md) for the current-vs-planned split.

---

## Architecture goals

| Goal | How the platform delivers it today |
|------|-----------------------------------|
| **Reproducible** | Exact `==` pins, committed `uv.lock`, `--frozen` installs — laptop, CI and image resolve identically. |
| **Observable** | JSON logs with request-id correlation; Prometheus `/metrics`; `@traced` emits real **OpenTelemetry spans** → Collector → Grafana Tempo ([ADR 0016](docs/adr/0016-observability-stack.md)). |
| **Typed & tested** | mypy `strict` and ruff from commit #1, enforced in CI. The suite is **hermetic by construction** — it cannot call a paid API ([ADR 0009](docs/adr/0009-hermetic-llm-testing.md)). |
| **Stable seams** | Proven repeatedly, not asserted: Stage 3 replaced the mock engine with a full agent stack behind the same `CompletionEngine` protocol; Stage 4 added retrieval as one more `Tool`; Stage 5 gave `@traced` a real OTel backend without touching a call site. None changed a route or the SSE format. Stage 8 then filled the `AuthProvider`/`Guardrail` contracts (auth, rate limiting, guardrails) as FastAPI dependencies around the same route — again without re-cutting it. |
| **Secure by default** | No secrets in git, env-only credentials, non-root container, errors that never leak internals. |
| **Honest** | Docs label planned work as planned. Stubs raise instead of faking. |

## Stack

**Runtime:** Python 3.12 · uv · FastAPI · Pydantic v2 · pydantic-settings · uvicorn · `langgraph==1.2.9` · `anthropic==0.116.0`
**Retrieval:** `llama-index-core==0.14.23` · `voyageai==0.5.0` · `qdrant-client==1.18.0`
**Observability:** `opentelemetry-{api,sdk}==1.43.0` · OTLP/HTTP exporter · OTel Collector · Grafana Tempo
**Data:** PostgreSQL · Redis · Qdrant (holds document vectors as of Stage 4)
**Ops:** Docker · Docker Compose · Prometheus · Grafana · GitHub Actions · Kubernetes · Helm · Terraform (AWS, validate-only)
**Quality:** pytest · ruff · mypy (strict) · pre-commit · RAG eval regression gate (Stage 6)

Every choice is justified in [ADR 0001](docs/adr/0001-stack-selection.md).
Two libraries were evaluated and **narrowed** rather than adopted wholesale:
LangChain was rejected for Stage 3 — the agent graph calls the Anthropic SDK
directly, so nothing imports it ([ADR 0006](docs/adr/0006-agent-loop-and-orchestration.md)).
Stage 4 took `llama-index-core`, **not** the `llama-index` meta-package, which
would have pulled a rival vendor's SDK into the base image to run an Anthropic +
Voyage stack ([ADR 0011](docs/adr/0011-embeddings-provider.md)).

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
  retrieval/       ✅ embeddings seam · Qdrant store · ingest · retriever ·
                      document_search tool (Stage 4)
  monitoring/      ✅ tracing seam — OTLP/Local providers, OTel span export (Stage 5)
  evaluation/      ✅ RetrievalEvaluator · metrics · dataset · baseline · LLM-judge (Stage 6)
  security/        ✅ ApiKeyAuthProvider · RedisRateLimiter · input/excerpt/egress guardrails (Stage 8)
shared/            ✅ config · logging · observability · datastores · migrations · version
data/corpus/       ✅ the RAG corpus — ingested by scripts/ingest.py (Stage 4)
data/eval/         ✅ eval dataset + regression baseline — scored by scripts/evaluate.py (Stage 6)
migrations/        ✅ forward-only raw SQL, applied on startup
config/            committed non-secret env profiles (dev/test/prod)
tests/             ✅ unit tests mirroring the source tree
examples/          runnable examples
infrastructure/    docker/ ✅   kubernetes/ ✅ Helm chart, kind-verified (Stage 7)   terraform/ ✅ AWS, validated-never-applied (Stage 7)
scripts/           helper scripts — ingest.py (costs $ outside test) · evaluate.py (RAG eval gate)
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
| 3 | Agents | [`stage-03-agents.md`](docs/stage-summaries/stage-03-agents.md) | [log](docs/verification-log/stage-03-agents.md) | ✅ complete |
| 4 | RAG | [`stage-04-rag.md`](docs/stage-summaries/stage-04-rag.md) | [log](docs/verification-log/stage-04-rag.md) | ✅ complete |
| 5 | Observability | [`stage-05-observability.md`](docs/stage-summaries/stage-05-observability.md) | — | ✅ complete |
| 6 | MLOps | [`stage-06-mlops.md`](docs/stage-summaries/stage-06-mlops.md) | — | ✅ complete |
| 7 | Kubernetes | [`stage-07-kubernetes.md`](docs/stage-summaries/stage-07-kubernetes.md) | [log](docs/verification-log/stage-07-kubernetes.md) | ✅ complete |
| 8 | Security | [`stage-08-security.md`](docs/stage-summaries/stage-08-security.md) | [log](docs/verification-log/stage-08-security.md) | ✅ **current — complete** |
| 9 | Reliability | `stage-09-reliability.md` | — | ⬜ next |
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
