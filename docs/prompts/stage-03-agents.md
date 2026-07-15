# Stage 3 — Agents

## Objective

Replace the mock `EchoEngine` with a real, LangGraph-orchestrated agent loop
that calls the Anthropic API, uses tools, tracks real token usage, and
persists conversation state — without changing the existing routes or SSE
plumbing.

## Scope

1. Implement the `Agent` and `Orchestrator` (currently stubs raising
   `NotImplementedError` in `services/agents` and `services/orchestrator`).
2. Promote the `orchestration` optional-dependency group (`langgraph`,
   `anthropic`, plus `langchain-anthropic` if the chosen integration needs
   it) to base dependencies in `pyproject.toml`. Pin exact versions per
   ADR 0001's convention. State the exact versions chosen in the
   self-report — do not assume any version is already decided, none is
   pinned anywhere yet.
3. An orchestrator-backed `CompletionEngine` implementing the existing
   protocol, swapped in behind the existing `get_engine` dependency. Routes
   and SSE plumbing do not change. Both non-streaming and streaming
   (`"stream": true`) paths must work against the real Anthropic API —
   streamed tokens map onto the existing SSE frame format from Stage 2
   (ADR 0004), not a redesigned one.
4. Real model calls to the Anthropic API. Real token accounting: populate
   actual `input_tokens`/`output_tokens` from the Anthropic response into
   the completion response's usage fields (Stage 2's `EchoEngine` faked
   this with a whitespace-split count — replace, don't wrap).
5. Tool use: a small tool registry (2-3 tools, deterministic, no reliance on
   live external APIs to pass tests). Agent loop: task -> reason -> select
   tool -> execute -> observe -> continue or return final answer. Keep tools
   domain-agnostic (generic engineering scope, not a named product).
   Retrieval/vector-search tools belong to Stage 4 — do not pull them
   forward; `services/retrieval` stays a stub.
6. Conversation state in Postgres (new schema — none exists yet; Stage 2
   left this explicitly deferred). Add migrations — pick an approach
   consistent with how the codebase already talks to Postgres (raw
   asyncpg, no ORM currently) and document the choice in an ADR, do not
   silently pick one. Caching in Redis: read-through cache for conversation
   history, source of truth stays Postgres — document the caching strategy
   in an ADR.

## API key handling

- `ANTHROPIC_API_KEY` from environment only. Add to `.env.example`, never to
  `.env` or committed anywhere.
- Extend the existing prod-boot-refusal pattern (ADR 0005: prod refuses to
  boot without all three datastore URLs) to also refuse boot without
  `ANTHROPIC_API_KEY` under the `prod` profile — same validator pattern, not
  a new mechanism.
- The `test` profile must not make real Anthropic API calls even if a key
  is present in the environment — inject a deterministic fake for the LLM
  client in test config, the same hermetic-by-construction pattern Stage 2
  used for datastores. State explicitly in the self-report how this is
  enforced — the mechanism, not just "mocked in tests."

## Required conventions (do not deviate)

- One ADR per non-trivial decision in `docs/adr/`, numbered sequentially
  continuing from the highest existing number (0005) — at minimum:
  LangGraph state/graph design, migration tool choice, Redis caching
  strategy, and how test-profile LLM mocking is enforced.
- `docs/PROJECT_STATUS.md` updated at end of stage — mark stage 3 complete,
  leave other stages' rows untouched.
- `CLAUDE.md` stays under 150 lines. State before/after line count in the
  self-report.
- `docs/architecture.md` is the source of truth — update it to reflect the
  agent loop, orchestrator, tool registry, Postgres schema, and Redis
  caching. Never hand-edit `architecture.html`; regenerate via
  `uv run python scripts/build_architecture.py`, and state that exact
  command in the self-report.
- Any new diagrams are pre-rendered static SVG, no CDN dependency
  (existing convention — keep following it).
- `pyproject.toml` keeps `[tool.uv] link-mode = "copy"` — do not remove.
- No hardcoded secrets anywhere.
- Stub folders for other stages (`retrieval`, `evaluation`, `monitoring`,
  `security`) are untouched this stage.

## Testing requirements

- All new code has unit test coverage. State the exact test count
  before/after using what `uv run pytest` actually reports, not a
  remembered number.
- Full suite passes with zero real network calls to Anthropic. CI must not
  require a real `ANTHROPIC_API_KEY` to go green.
- `ruff check .`, `ruff format --check .`, and `mypy` (strict) all clean.
  State the exact commands run and their actual exit status, not just
  "passed."
- One integration test exercising a full agent run end to end (tool use +
  Postgres persistence + Redis cache) with the Anthropic client faked, not
  the whole engine stubbed out.

## Explicit constraints

- Do not commit or push. Build, test, self-report only — commit happens
  only after independent manual verification, as a separate step.
- Do not touch stub folders for other stages.
- Use the canonical stage name `stage-03-agents` verbatim everywhere.

## Self-report requirements

Write `docs/stage-summaries/stage-03-agents.md` containing:

- What was implemented, file by file
- Exact test count before/after and the command used
- Exact ruff/mypy/format commands run and their actual results
- CLAUDE.md line count before/after
- The exact package versions pinned for the orchestration promotion
- The migration tool chosen and why (pointer to the ADR)
- The Redis caching strategy chosen and why (pointer to the ADR)
- Exactly how test-profile LLM mocking is enforced (mechanism, not claim)
- Confirmation `docs/architecture.md` was updated and
  `scripts/build_architecture.py` was re-run, with the exact command
- Any deviations from this scope and why
- Known limitations or deliberately deferred items

State only what was directly run and observed this stage. Do not restate
prior-stage claims as if re-verified.

## Note

This stage calls a paid external API (Anthropic) once wired into
`dev`/`prod`. Flag this explicitly in your understanding-of-scope paragraph
before starting, per the "never call a paid external API without explicit
human confirmation" rule.
