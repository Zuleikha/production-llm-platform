Stage 2 — FastAPI Service and Shared Libraries

Project: production-llm-platform
Repo: D:\GIT-REPOS\production-llm-platform
GitHub: github.com/Zuleikha/production-llm-platform

1. Continuity — read first, before any code

Read CLAUDE.md in full before doing anything else. It points to the roadmap and ADRs rather than duplicating detail — follow those pointers.

Also read:
- docs/PROJECT_STATUS.md — canonical roadmap and progress tracker
- docs/stage-summaries/stage-01-foundation.md — what stage 1 actually built
- docs/adr/0001-stack-selection.md, 0002-repo-structure.md, 0003-configuration-approach.md

Stage 1 delivered: FastAPI app with /health, /ready stub, /version, /metrics; JSON structured logging with request-id correlation; typed layered config (dev/test/prod); a 6-container Compose stack (api, postgres, redis, qdrant, prometheus, grafana); CI (lint, types, tests, Docker health); 6 later-stage service folders as stubs raising NotImplementedError (agents, evaluation, monitoring, orchestrator, retrieval, security).

Deferred from stage 1, now in scope for stage 2: chat/completion endpoints, streaming responses, real DB/Redis/Qdrant connection wiring, and a /ready endpoint that does real dependency probes instead of a stub.

2. Prerequisite check — verify before assuming installed

Do not assume tools are present. Check each of these and show real command output:

- uv --version
- docker --version and docker compose version
- git --version
- python --version (must resolve through the uv-managed interpreter, not the system D:\Python\Python312 build — see CLAUDE.md known quirks)

If anything is missing, give the Windows install command (winget or the official installer), run it, then re-verify. If a step cannot be automated (e.g. a GUI installer, a Windows feature toggle, a PATH change requiring a new shell), stop and ask rather than guessing.

Known environment quirks already documented in CLAUDE.md — do not rediscover these, just respect them:
- UV_LINK_MODE=copy required (cross-drive cache on C:, repo on D:)
- Never use the system Python at D:\Python\Python312 — use the uv-managed 3.12.13 interpreter
- sniffio must stay pinned as an explicit direct dependency in pyproject.toml
- Grafana host port is 3001, not 3000 (open-webui holds 3000 on this machine) — do not revert docker-compose.yml's "3001:3000" mapping

3. Stub-only rule for later-stage folders

services/agents, services/evaluation, services/monitoring, services/orchestrator, services/retrieval, services/security stay exactly as stage 1 left them — stub contracts raising NotImplementedError. Do not add real logic to any of them in this stage, even if it feels natural while wiring the API layer. If a chat/completion endpoint needs an orchestration call, stub the call boundary and leave the real implementation for stage 3.

4. Scope for this stage

- Chat/completion endpoint(s) on the FastAPI service — request/response schemas, validation, error handling. No real LLM call yet (that's stage 3 with LangGraph); return a mocked or echo response so the contract and tests are real.
- Streaming response support (SSE or chunked, your call — document the choice in an ADR) for the above endpoint, even against the mocked response, so the plumbing is proven before stage 3 wires a real model in.
- Real DB/Redis/Qdrant connection wiring: connection pooling, startup/shutdown lifecycle hooks, config already exists in .env.example (currently commented out) — uncomment and wire it up.
- /ready endpoint doing real dependency probes (DB ping, Redis ping, Qdrant ping) — distinct from /health, which stays a pure liveness check with no dependency calls.
- Pin any new dependency to an exact version in pyproject.toml (uv add <package>==<version>), no unpinned ranges. Document why each new dependency was chosen if it's not obvious (a one-line note in the relevant ADR is enough, doesn't need its own ADR unless it's architecturally significant).

5. Verification bar — real output, not claims

For every check below, paste actual terminal output. A claim without the output attached does not count as done.

- uv run ruff check .
- uv run ruff format --check .
- uv run mypy --strict .
- uv run pytest
- docker compose up -d --build
- docker compose ps (all 6 containers healthy, including the new DB/Redis/Qdrant dependency checks reflected in /ready)
- curl -i http://localhost:8000/health
- curl -i http://localhost:8000/ready (must actually hit the datastores, not just return 200 unconditionally)
- a real curl or httpie call to the new chat/completion endpoint showing streaming output, not just a unit test asserting it
- grep check for hardcoded secrets across any new config/connection code
- git status clean at the end, nothing stray

6. ADR and stage-summary conventions

- One ADR per significant decision in docs/adr/, next sequential number (0004, 0005, ...). Candidates this stage: streaming transport choice (SSE vs chunked), connection pooling approach for DB/Redis/Qdrant.
- docs/stage-summaries/stage-02-fastapi-service.md — same fixed structure as stage 1's: objective, features, decisions, deps, validation output (paste real output here too), limitations, tech debt, deferred items, prerequisites for stage 3.
- Do not touch docs/verification-log/ — that's mine, not yours to write.

7. Handover step before stopping

Before ending the session:
- Write docs/stage-summaries/stage-02-fastapi-service.md
- Update docs/PROJECT_STATUS.md (mark stage 2 complete, 2/10)
- Confirm CLAUDE.md is still under 150 lines; if new quirks were discovered, add them the same way the port-3000 and sniffio ones were added
- Stop and wait. Do not start stage 3 work. I will run my own independent verification before authorizing that.

Model note: this is a mechanical/scaffolding stage, run it with the default model, no need to escalate.