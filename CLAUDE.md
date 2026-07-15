# CLAUDE.md — project memory

Read at the start of every session. Keep under ~150 lines. Facts only — rationale
lives in `docs/adr/`.

## Current state — **Stage 3 of 10 (Agents), COMPLETE.** Version `0.1.0`

`POST /v1/chat/completions` runs a **real LangGraph agent loop against the
Anthropic API** (`claude-opus-4-8`): reason → tool → observe → answer, bounded by
`agent_max_steps`. Real token usage, summed across every model call. History
persists to Postgres behind a Redis cache when the request carries a
`conversation_id`; without one it is stateless as in Stage 2. Stage 2's
`EchoEngine` is **gone** — the `CompletionEngine` seam held; routes and SSE wire
format unchanged. **No RAG, no auth** — still stubs. Qdrant is probed, holds no
data (Stage 4).

Roadmap: **`docs/PROJECT_STATUS.md`** (canonical). Stage detail:
`docs/stage-summaries/stage-0{1-foundation,2-api,3-agents}.md`. Rationale:
**`docs/adr/`** — 0001 stack · 0002 structure · 0003 config · 0004 streaming ·
0005 pooling · 0006 agent loop · 0007 migrations · 0008 caching · 0009 hermetic
LLM tests · 0010 pre-rendered diagrams.

## Layout

```
services/api/    app.py · routes/{health,meta,chat} · schemas.py
                 completions.py = CompletionEngine seam + OrchestratorEngine
services/agents/ base.py (Agent · ToolAgent) · tools.py (registry, 3 tools)
services/orchestrator/  base.py (Orchestrator · AgentOrchestrator) · graph.py
                 (LangGraph) · llm.py (LLMClient seam) · conversations.py
services/{retrieval,evaluation,monitoring,security}/  ⬜ stubs
shared/          config · logging · observability · datastores · migrations · version
migrations/      NNNN_name.sql — forward-only, applied in the lifespan · tests/ mirrors source
docs/diagrams/   GENERATED SVG · architecture.html GENERATED from architecture.md
```

## Conventions

- **Stubs:** future-stage folders get a `README.md` (contract + owning stage) plus
  an ABC/Protocol whose methods `raise NotImplementedError`. Never a mini-version.
- **Naming:** `snake_case` modules/functions, `PascalCase` classes, `_private`.
  ADRs `NNNN-kebab-title.md`. Stage summaries + verification logs have **fixed**
  filenames — see PROJECT_STATUS. `config/environments/*.env` are committed,
  NON-SECRET.
- **Config:** one `Settings` (pydantic-settings) via `get_settings()` (`lru_cache`d).
  Precedence: OS env > `.env` > `config/environments/<ENV>.env` > defaults. **No
  secrets committed** — OS env only (a test enforces it). **`prod` requires all three
  datastore URLs + `ANTHROPIC_API_KEY`**; `test` ignores the root `.env`.
- **Datastores:** `DatastoreRegistry.from_settings()` on `app.state.datastores`. No
  URL = `not_configured`, never dialled. `startup()` is concurrent and **never
  raises** — failures surface on `/ready`, not a crash loop. `/health` must **never**
  probe (tripwire enforces).
- **LLM — read ADR 0009 before touching:** the `test` profile **cannot construct**
  `AnthropicClient`; it raises before the key is read. Always go via
  `build_llm_client(settings)`. The guard keys on the *profile*, not the key's
  absence, so a dev with `ANTHROPIC_API_KEY` exported gets CI's exact hermetic
  suite. Never replace this with "remember to mock it".
- **No sampling params.** Claude Opus 4.7+ **rejects `temperature`/`top_p`/`top_k`
  with a 400**; `budget_tokens` is gone (use `thinking={"type":"adaptive"}`).
  `ChatCompletionRequest.temperature` is accepted for wire compat and deliberately
  **not forwarded**. `max_tokens` is **required** on every API call.
- **Agent:** routes call the `CompletionEngine` on `app.state.engine`;
  `create_app(settings, engine=...)` overrides it (and stops the lifespan rebuilding
  it). Tools must be **deterministic and offline** — the suite runs the real loop.
  `calculator` walks an AST allow-list; **never `eval`** — the model is not a trusted
  caller. A failing tool returns `is_error` rather than raising.
- **Persistence:** Postgres is the source of truth, Redis only caches. Writes
  **invalidate** (Postgres first, then `DELETE`) — never rewrite. A Redis failure
  degrades to a Postgres read: the **one** sanctioned exception to fail-loud
  (ADR 0008). Migrations are plain SQL, forward-only, advisory-locked.
- **Logging:** `get_logger(__name__)`; log **events** not sentences —
  `_logger.info("http.request", extra={...})`. One JSON object per line. Never log
  PII/secrets/tokens. **Tracing:** `@traced` on new application functions; **exempt:**
  `shared/{logging,observability}.py` (recursion), async generators/lifespan, and
  LangGraph nodes (the graph inspects signatures).
- **Errors:** fail loud. Envelope `{"error": {type, message, request_id}}`; 500s never
  leak internals. Handlers on **Starlette's** `HTTPException`.
- **Types:** mypy `strict`, everything annotated incl. tests. Narrow with `isinstance`
  not `# type: ignore`. Depend on narrow Protocols (`CacheBackend`, `LLMClient`) not
  concrete driver types. **Deps:** `==` pins, `uv.lock` committed, `--frozen` installs.
- **Tests:** `tests/unit/`, mirror source. Test contracts, not internals. Write the
  failing test first; never edit a test to make it pass. Switching profiles needs
  `monkeypatch.setenv` **+** `get_settings.cache_clear()`.
- **Container boot is required, not deferrable.** Before self-report, `docker build`
  and run the image in **both** `prod` and `test` profiles, and curl the endpoints
  the stage touched. A green `pytest` does not prove the container boots: Stage 3
  deferred this as a "known gap" and CI then caught two real failures a 2-minute
  local boot catches instantly (`docs/verification-log/stage-03-agents.md`).
- **Architecture doc:** `docs/architecture.md` is the source; `architecture.html` is
  **generated** by `scripts/build_architecture.py` — never edit the HTML. Diagrams
  are **pre-rendered SVG** in `docs/diagrams/` — no CDN, no JS (ADR 0010). Changing
  one needs Node/`npx`; building + `--check` need only Python. A Mermaid keyword
  (`graph`, `end`) as a node id fails the render. **Every stage updates the md +
  regenerates** — a test fails on drift.

## Run / test / lint

```bash
uv sync                                        # install
export ANTHROPIC_API_KEY=sk-ant-...            # real calls only; never dev/test
uv run uvicorn services.api.app:app --reload   # API only  -> :8000
docker compose up -d --build                   # full stack
pwsh scripts/verify.ps1   # or ./scripts/verify.sh — the whole gate
uv run ruff check . --fix ; uv run ruff format . ; uv run mypy ; uv run pytest -v
uv run python scripts/build_architecture.py   # regenerate (needs npx for diagrams)
docker build -t plp/api:local . && docker run -d -p 8010:8000 -e ENVIRONMENT=test plp/api:local
```

Endpoints: `/health` `/ready` `/version` `/metrics` `/docs` · `POST /v1/chat/completions`
(`"stream": true` for SSE; optional `conversation_id`). Prometheus :9090 · Grafana :3001.

## Known environment quirks — this machine, not the code

- **Cross-drive uv / lock-install mismatch.** uv cache on `C:`, repo on `D:`. uv's
  default hardlink mode fails across drives **silently** — the package stays in
  `uv.lock` but never lands in the venv, surfacing as a baffling
  `ModuleNotFoundError`. Fixed repo-wide by `link-mode = "copy"` under `[tool.uv]`
  — do not remove. If a locked package won't import, diff `uv pip list` against
  `uv.lock` first. `sniffio` is an explicit direct dep from when this bit.
- **Never use the system Python at `D:\Python\Python312`.** Stripped build: a venv
  from it **segfaults on `import ctypes`** (surfaces as an access violation importing
  `httpx`). Use `uv python install 3.12 && uv venv --managed-python --python 3.12`;
  the venv runs managed CPython **3.12.13**.
- **Grafana port 3000 conflicts with `open-webui`.** Remapped to **3001** in
  `docker-compose.yml`; check `docker ps` for collisions first. **Docker Desktop
  must be running** for live-datastore tests — `docker compose version` works
  without the daemon, `docker ps` is the real check.

## Deferred — do NOT build early

| Stage | Deferred |
|:--:|---|
| 4 | RAG — `Retriever`/`VectorStore`, LlamaIndex, `qdrant-client`, a retrieval tool · **5** OTel export, Grafana dashboards, alerting |
| 6 | Evaluation (`Evaluator`), MLOps pipelines · **7** K8s manifests, Terraform · **8** Auth, guardrails, rate limiting — **API is unauthenticated** |
| 9 | Load/chaos testing, SLOs, pool tuning, circuit breaking, prompt caching, context compaction · **10** Portfolio polish |

## Known issues

- Starlette 1.3.1 deprecates `httpx` for `TestClient` (warning only). 422 stringifies
  Pydantic's raw error list into `message`. `config/environments/*.env` can drift from
  `.env.example`. A datastore failing `connect` at boot stays `unavailable` until restart.
- **`/ready` does not check schema version** — a failed migration leaves the
  service reporting ready while every chat query fails (ADR 0007).
- **No per-conversation concurrency control** — two concurrent turns on one
  `conversation_id` collide on `(conversation_id, position)`; the second fails
  loudly, not serialised (ADR 0008).
- **The hermetic suite cannot catch a wrong assumption about the real Anthropic
  API** — the fake encodes our beliefs. Make a real call when changing `llm.py`
  (ADR 0009).
