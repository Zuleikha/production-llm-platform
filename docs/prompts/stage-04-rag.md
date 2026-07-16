# Stage 4 — RAG

## Objective

Ground the agent's answers in retrieved documents with citations, without
changing the existing routes, SSE plumbing, or agent loop shape.

## Scope

1. Implement `Retriever` and `VectorStore` (currently stubs raising
   `NotImplementedError` in `services/retrieval`).
2. Promote the `retrieval` optional-dependency group (LlamaIndex ingestion
   components, `voyageai` embeddings client, `qdrant-client`) to base
   dependencies in `pyproject.toml`. Pin exact versions per ADR 0001's
   convention. State the exact versions chosen in the self-report — none is
   pinned anywhere yet.
3. LlamaIndex-based ingestion and chunking pipeline: turn source documents
   into embedded chunks. Keep the ingestion source domain-agnostic (generic
   engineering scope, not a named product) — a small fixture corpus is
   sufficient for tests, do not depend on a live external document source
   to pass the suite.
4. Embeddings: **Voyage AI** (Anthropic's own docs recommend Voyage for
   pairing with Claude on RAG workloads — no in-house embeddings API exists;
   confirmed, do not re-litigate the choice). Pick the specific model (e.g.
   `voyage-3` family) and document it in an ADR. `VOYAGE_API_KEY` from
   environment only, same as `ANTHROPIC_API_KEY` — add to `.env.example`,
   extend the `prod`-boot-refusal validator (ADR 0005/0006 pattern) to
   require it too. Route embedding calls through a new `build_embeddings_client`
   seam mirroring Stage 3's `build_llm_client` — the `test` profile must not
   be able to construct a real Voyage client, by construction, not by
   convention. State the mechanism explicitly in the self-report, not just
   "mocked in tests."
5. Qdrant-backed vector search: swap the Stage 2 `httpx` `/readyz`-only probe
   for a real `qdrant-client` behind the existing `Datastore` contract (ADR
   0005). Qdrant has been connected and probed since Stage 2 but has held no
   data — this stage is what puts data in it.
6. A retrieval tool added to the agent's existing `ToolRegistry` (Stage 3) —
   this is a new `Tool`, not a new loop or a new orchestration mechanism.
   The agent loop, `AgentGraph`, and `LLMClient` seam do not change shape.
7. Citations: retrieved chunks that ground an answer are surfaced in the
   response in a way a client can trace back to source documents. Define
   the citation shape (new response field, not overloading an existing one)
   and document it.
8. Conversation/schema: add `migrations/0002_*.sql` if ingestion metadata or
   citation records need persistence — follow the existing forward-only,
   advisory-locked migration pattern (ADR 0007), do not introduce a second
   migration mechanism.

## Security — read before writing the retrieval tool

Stage 3's tools are pure functions of their arguments, which is what made it
safe to feed tool results straight back into the model unexamined. A
retrieval tool returns **document text** — potentially attacker-controlled —
so prompt injection through tool results is a live concern the moment this
lands, not a theoretical one deferred to Stage 8. At minimum:

- Document the threat explicitly in `services/agents/README.md` and in the
  retrieval tool's own module, at the point where someone will read it before
  extending it.
- State in the self-report what mitigation (if any) was applied — e.g.
  delimiting retrieved content from instructions, stripping/flagging
  instruction-like patterns in chunks, or explicitly deferring with a named
  reason. Do not silently ship an unexamined pass-through.
- This is a documented decision, not full prompt-injection hardening —
  scoping the mitigation depth is part of this stage's judgment calls; state
  what was chosen and why.

## Contract test — closing the Stage 3 gap

Stage 3 shipped with no test against the real Anthropic API — CI proves the
HTTP surface only, so a provider-side field rename (usage keys, event names,
response shape) would pass CI undetected. Stage 4 adds a second unverified
external surface (the Voyage AI embeddings API) on top of that gap, so this
stage closes both:

- Add one opt-in, manually-triggered live test (not run in CI, same
  opt-in pattern as the existing live-datastore integration tests) that
  makes one real call each to the Anthropic chat API and the Voyage AI
  embeddings API, asserting the response shape the code assumes.
- State in the self-report: the exact commands run, the actual output, and
  the approximate cost (same disclosure level as Stage 3's two live calls).
- Do not make any live call without explicit human confirmation first, per
  the standing rule.

## Required conventions (do not deviate)

- One ADR per non-trivial decision in `docs/adr/`, numbered sequentially
  continuing from the highest existing number (0010) — at minimum: Voyage AI
  model choice, Qdrant collection/schema design, citation shape, and the
  prompt-injection mitigation decision above.
- `docs/PROJECT_STATUS.md` updated at end of stage — mark stage 4 complete,
  leave other stages' rows untouched.
- `CLAUDE.md` stays under 150 lines. State before/after line count in the
  self-report.
- `docs/architecture.md` is the source of truth — update it to reflect
  ingestion, the retriever, Qdrant now holding data, the retrieval tool, and
  the citation flow. Never hand-edit `architecture.html`; regenerate via
  `uv run python scripts/build_architecture.py`, and state that exact
  command in the self-report.
- Any new diagrams are pre-rendered static SVG, no CDN dependency (ADR 0010
  convention — keep following it). Do not use a Mermaid keyword (`graph`,
  `end`, etc.) as a node id — it fails the render (documented sharp edge from
  Stage 3).
- `pyproject.toml` keeps `[tool.uv] link-mode = "copy"` — do not remove.
- No hardcoded secrets anywhere.
- Stub folders for other stages (`evaluation`, `monitoring`, `security`) are
  untouched this stage.
- **Container boot is required before self-report, not deferrable.** Stage 3
  deferred this as a "known gap" and CI caught two real failures a 2-minute
  local boot would have caught instantly. Before writing the self-report:
  `docker build` and run the image in both `prod` and `test` profiles, and
  curl the endpoints this stage touched (including the retrieval-augmented
  chat path). State the exact commands and their actual output.

## Testing requirements

- All new code has unit test coverage. State the exact test count
  before/after using what `uv run pytest` actually reports, not a
  remembered number.
- Full suite passes with zero real network calls (Anthropic or Voyage AI).
  CI must not require real API keys to go green.
- `ruff check .`, `ruff format --check .`, and `mypy` (strict) all clean.
  State the exact commands run and their actual exit status, not just
  "passed."
- One integration test exercising ingestion → embed → store → retrieve →
  cite end to end, with the embeddings/Anthropic clients faked, against a
  real Qdrant (same pattern as Stage 3's live-Postgres integration test).
- The opt-in live contract test from the section above, run manually this
  session with output captured.

## Explicit constraints

- Do not commit or push. Build, test, self-report only — commit happens
  only after independent manual verification, as a separate step.
- Do not touch stub folders for other stages.
- Use the canonical stage name `stage-04-rag` verbatim everywhere.

## Self-report requirements

Write `docs/stage-summaries/stage-04-rag.md` containing:

- What was implemented, file by file
- Exact test count before/after and the command used
- Exact ruff/mypy/format commands run and their actual results
- CLAUDE.md line count before/after
- The exact package versions pinned for the retrieval promotion
- The Voyage AI model chosen and why (pointer to the ADR)
- The Qdrant collection/schema design and why (pointer to the ADR)
- The citation shape and why (pointer to the ADR)
- The prompt-injection mitigation applied (or explicitly deferred, with
  reason) — mechanism, not claim
- Exactly how test-profile Voyage AI mocking is enforced (mechanism, not
  claim)
- Confirmation `docs/architecture.md` was updated and
  `scripts/build_architecture.py` was re-run, with the exact command
- Confirmation the container was built and booted in both `prod` and `test`
  profiles, with the exact commands and curl output
- The live contract test commands run and their actual output, with
  approximate cost
- Any deviations from this scope and why
- Known limitations or deliberately deferred items

State only what was directly run and observed this stage. Do not restate
prior-stage claims as if re-verified.

## Note

This stage calls paid external APIs (Voyage AI embeddings, and the live
contract test above, which also hits the Anthropic chat API) once wired into
`dev`/`prod`. Flag this explicitly in your understanding-of-scope paragraph
before starting, per the "never call a paid external API without explicit
human confirmation" rule.