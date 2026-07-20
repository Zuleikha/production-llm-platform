# Stage 6 — MLOps

## Objective

Give the RAG pipeline (Stage 4) a real, repeatable evaluation harness and an
automatic regression gate in CI — not a restated health check. Implement the
`Evaluator` stub (`services/evaluation/base.py`).

## Decided before this stage (do not re-litigate)

- **Scope: RAG only.** Score retrieval + grounded-answer quality against the
  existing `data/corpus/` fixture set. Not agent tool-use, not general chat
  quality — those have no existing fixture corpus to evaluate against and are
  out of scope this stage.
- **Two-tier evaluation, split by what's CI-safe:**
  - **Tier 1 — deterministic retrieval metrics (recall@k, MRR)**, computed
    using the same offline-hash embeddings the `test` profile already uses
    (ADR 0011) — free, hermetic, zero network calls. **This is the CI-blocking
    regression gate.**
  - **Tier 2 — LLM-as-judge** (answer faithfulness, citation accuracy),
    which makes real Anthropic API calls. This is **opt-in / manually
    triggered only**, same treatment as the Stage 4 live contract test (ADR
    0015) — never a required CI gate, never runs without explicit human
    confirmation, never needs `ANTHROPIC_API_KEY` in CI.
- **Not a new API route.** The eval harness runs as an operator script
  (`scripts/evaluate.py`), same pattern as `scripts/ingest.py` — never a boot
  hook, never wired into the running service. This is a dev/CI-time tool, not
  part of the product's API surface.
- **Fix a doc inconsistency while you're in there:** `CLAUDE.md`'s "Deferred"
  table currently lists "OTel metrics export" under Stage 6, but
  `docs/PROJECT_STATUS.md` (canonical) defines Stage 6 as MLOps/eval only —
  the two disagree. Move that deferred item to Stage 9 (which already owns
  SLOs and actually needs metrics) in both `CLAUDE.md` and anywhere else it's
  referenced. State this fix explicitly in the self-report.

Write one ADR (0017, continuing sequentially) covering: the recall@k/MRR
metric choice, the CI-blocking-vs-opt-in split above, and the regression
baseline mechanism (next section).

## Scope

1. **Implement `Evaluator`** (`services/evaluation/base.py` — currently
   raises `NotImplementedError`) with a concrete class scoring a dataset of
   query → expected-relevant-document-id cases against the live retrieval
   pipeline (`services/retrieval/retriever.py`), returning named metrics
   (`recall_at_k`, `mrr`, at minimum).

2. **Eval dataset**: a small, checked-in fixture set of queries with known
   relevant document ids from `data/corpus/` — same spirit as the existing
   RAG fixture corpus, deterministic, no live external dependency to run.
   Document how each case's "expected relevant doc" was chosen (even briefly)
   so the dataset isn't arbitrary.

3. **`scripts/evaluate.py`**: operator script, same shape as
   `scripts/ingest.py` — runs the Tier 1 deterministic metrics against the
   eval dataset and prints/writes a report. A separate flag or script
   (clearly gated, matching the ADR 0015 opt-in pattern) runs Tier 2
   LLM-as-judge scoring — never on by default, never in CI.

4. **Regression baseline**: a checked-in baseline file (e.g.
   `data/eval/baseline.json` or similar — your choice, document it) recording
   the last-accepted Tier 1 scores. The CI gate fails if a run's scores drop
   below baseline minus a documented tolerance. Updating the baseline itself
   is a deliberate, reviewed change (a human decides scores genuinely
   improved), never auto-overwritten by a passing run.

5. **CI wiring**: add the Tier 1 regression check to `.github/workflows/ci.yml`
   as a required job — hermetic, no API keys needed, same standard the rest
   of CI already holds.

6. **Stub folder cleanup**: `services/evaluation/README.md` gets updated from
   "planned, not yet implemented" to reflect what's actually built, same
   pattern as Stage 4/5 did for their own stub folders.

## Required conventions (do not deviate)

- One ADR (0017, continuing sequentially) — the combined eval-design decision
  from the section above.
- `docs/PROJECT_STATUS.md` updated at end of stage — mark stage 6 complete,
  leave other stages' rows untouched.
- `CLAUDE.md` stays under 150 lines. State before/after line count. Include
  the deferred-table fix (OTel metrics: Stage 6 → Stage 9) described above.
- `docs/architecture.md` updated to reflect the eval harness and regression
  gate; regenerate via `uv run python scripts/build_architecture.py` and
  state the exact command. Never hand-edit `architecture.html`.
- `pyproject.toml` keeps `[tool.uv] link-mode = "copy"`.
- No hardcoded secrets anywhere.
- Stub folders for other stages (`security`) are untouched this stage.
- **Container boot is required before self-report, not deferrable.** Bring
  up both `prod` and `test` profiles, curl the endpoints — the eval harness
  itself is a script, not an endpoint, so this check confirms it did **not**
  accidentally get wired into app startup. State the exact commands and
  output.

## Testing requirements

- All new code has unit test coverage. State exact test count before/after
  using what `uv run pytest` actually reports.
- Tier 1 metrics computation is tested against a small, known-answer fixture
  case where recall@k/MRR can be hand-verified — not just "it runs."
- Full suite passes with zero real network calls. CI must not require
  `ANTHROPIC_API_KEY` to go green — Tier 2 is excluded from the default test
  run and from CI, consistent with the opt-in design above.
- `ruff check .`, `ruff format --check .`, `mypy` (strict) all clean. State
  exact commands and actual exit status.
- One test proving the CI regression gate actually fails on a deliberately
  regressed input (e.g. a corrupted/shrunk retrieval result) — not just that
  it passes on the happy path.

## Explicit constraints

- Do not commit or push. Build, test, self-report only — commit happens only
  after independent manual verification, as a separate step.
- Do not touch the `security` stub folder.
- Use the canonical stage name `stage-06-mlops` verbatim everywhere.
- Tier 2 (LLM-as-judge) makes real, billable Anthropic API calls when
  manually run. Do not run it without explicit human confirmation first, per
  the standing rule — state this explicitly in your understanding-of-scope
  paragraph before starting.

## Self-report requirements

Write `docs/stage-summaries/stage-06-mlops.md` containing:

- What was implemented, file by file
- Exact test count before/after and the command used
- Exact ruff/mypy/format commands run and their actual results
- CLAUDE.md line count before/after, and confirmation the deferred-table fix
  (OTel metrics → Stage 9) was made
- The recall@k/MRR metric choice and why (pointer to ADR 0017)
- The eval dataset's cases and how "expected relevant doc" was determined
  for each
- The regression baseline mechanism and file location
- Confirmation the CI regression gate actually fails on a regressed input
  (from the testing requirements above) — mechanism, not claim
- Confirmation `docs/architecture.md` was updated and
  `scripts/build_architecture.py` was re-run, with the exact command
- Confirmation the container was built and booted in both `prod` and `test`
  profiles, with the exact commands and curl output, and that the eval
  harness is confirmed NOT wired into app startup
- If Tier 2 (LLM-as-judge) was run this session: exact commands, output, and
  approximate cost. If not run, state that explicitly rather than silently
  skipping it.
- Any deviations from this scope and why
- Known limitations or deliberately deferred items

State only what was directly run and observed this stage. Do not restate
prior-stage claims as if re-verified.
