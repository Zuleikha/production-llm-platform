You are acting as a Senior Staff MLOps Engineer and Platform Architect.

STAGE 1 of 10: Foundation and tooling.
Project: production-llm-platform.

CONTINUITY FROM PREVIOUS STAGES
This is the first stage. No CLAUDE.md, ADR log, or repository exists yet.
You are creating all of it from scratch.

PREREQUISITE CHECK
Before writing any code, check the local Windows development environment
for the following. Do not assume anything is installed.

1. Python 3.12 -> verify: python --version
2. uv -> verify: uv --version
3. Docker Desktop with Compose v2 (WSL2 backend) -> verify: docker --version
   and docker compose version
4. git -> verify: git --version

For each missing tool:
- Explain why it is needed and what it will be used for in this stage
- Provide the Windows install command (prefer winget, fall back to the
  official installer link if winget has no package)
- Provide the verification command to confirm success
- If installation cannot be automated (e.g. Docker Desktop requires a GUI
  step, WSL2 enablement, or a reboot), STOP and ask me to complete the
  manual step, then wait for my confirmation before continuing
- Do not install anything silently, list what you are about to run first
- Do not proceed past a failed or unconfirmed prerequisite

Postgres, Redis, Qdrant, Prometheus, and Grafana run as Docker containers
in this stage, they do not need host-level installation, only Docker
itself needs to be present and running.

Only proceed to implementation once every prerequisite above is confirmed
installed and verified.

PRIMARY GOAL
Create only the project foundation. Do not implement business logic yet.
Where a folder's real content belongs to a later stage (agents/, retrieval/,
evaluation/, monitoring/, security/), create an interface stub only:
README.md describing the intended contract, and where relevant an
abstract base class or Protocol with docstrings and no implementation.
A stub that raises NotImplementedError is correct, a working mini version
of that component is not.

TECH STACK (pin exact versions in pyproject.toml, no floating latest)
Python 3.12, uv, FastAPI, Pydantic v2, LangChain, LangGraph, LlamaIndex,
Docker, Docker Compose, PostgreSQL, Redis, Qdrant, Prometheus, Grafana,
OpenTelemetry, pytest, ruff, mypy, pre-commit, GitHub Actions.

REPO STRUCTURE
docs/{architecture,adr,runbooks,stage-summaries}, services/{api,orchestrator,
agents,retrieval,evaluation,monitoring,security}, shared/, config/, tests/,
examples/, infrastructure/{docker,kubernetes,terraform}, .github/workflows/,
scripts/

FASTAPI SERVICE
health, readiness, metrics, version endpoints. Structured JSON logging with
timestamp, request ID, service, environment, level, message, exception
trace. Config via pydantic-settings, dev/test/prod profiles, no hardcoded
secrets. Startup/shutdown hooks. Global error handler.

TESTING / QUALITY
pytest with tests for health endpoint and config loading. ruff, mypy
(strict mode), pre-commit, EditorConfig, gitignore.

DOCKER
Dockerfile (multi-stage, non-root user), docker-compose.yml wiring api,
postgres, redis, qdrant, prometheus, grafana. Verification bar: docker
compose up succeeds and GET /health returns 200 from the api container,
not just docker build succeeding.

CI
GitHub Actions: install, lint, type check, unit tests, docker build
verification. Must fail on lint or test failure.

ARCHITECTURE DECISION RECORDS
Create one ADR per significant decision under docs/adr/, not a single
combined file. At minimum: 0001-stack-selection.md, 0002-repo-structure.md,
0003-configuration-approach.md. Standard ADR format: context, decision,
consequences, status.

STAGE SUMMARY NAMING CONVENTION (fixed for the entire project)
Establish this exact roadmap now, it applies to all 10 stages. Do not
create the files for stages 2 through 10, only record the convention so
every future stage prompt uses the correct filename with no drift:

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

Record this list verbatim in docs/PROJECT_STATUS.md as the canonical
roadmap, and add a pointer to it in CLAUDE.md.

PROJECT MEMORY
Create CLAUDE.md at repo root. Keep it under roughly 150 lines, it is read
at the start of every future session and must not balloon. Include:
current stage and state, active conventions (naming, folder layout, config
pattern, logging format, test structure), how to run/test/lint locally,
explicit list of what is deferred to later stages, pointer to docs/adr/
for rationale, and pointer to the stage summary roadmap above.

DOCUMENTATION
README (overview, architecture goals, stack, setup, running locally,
testing, repo structure, roadmap listing all 10 stages by name and their
fixed summary filenames, current stage marked).
docs/architecture.md: current state vs planned (label future components
"planned, not yet implemented" explicitly, don't describe them as if built).
docs/development.md, docs/contributing.md, docs/coding-standards.md.

STAGE HANDOVER (do this last, before stopping)
Create docs/stage-summaries/stage-01-foundation.md containing:
1. Objective
2. Features implemented
3. Repository structure created
4. Architecture decisions and rationale
5. Dependencies added and why they were chosen
6. Key configuration decisions
7. Files created or modified
8. Validation completed (tests, lint, type checking, Docker build, actual
   command output pasted in, not a claim that it passes)
9. Known limitations
10. Technical debt
11. Assumptions made
12. Items intentionally deferred to later stages
13. Prerequisites for Stage 2

Create or update docs/PROJECT_STATUS.md containing:
- Current project version
- Completed stages (just stage 1 so far)
- Remaining stages (2 through 10, listed with their fixed filenames)
- Current repository capabilities
- Planned next milestone (stage 2 objective)
- Overall progress percentage (1/10)

This documentation is the source of truth for every future stage prompt.

OUTPUT REQUIREMENTS
1. Full repo tree
2. Every dependency added and why
3. Every major design decision and why
4. What was intentionally deferred and to which stage
5. Verification: uv sync, pytest, ruff, mypy, docker compose up + health
   check, actual output shown for each, not a claim that it passes
6. Confirmation that CLAUDE.md, ADRs, stage-01-foundation.md, and
   PROJECT_STATUS.md were all created

Stop after the handover documents are written. Do not begin stage 2.