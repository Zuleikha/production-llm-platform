# Stage 10 — Portfolio

## Objective

Package what stages 1–9 built into a repo a hiring reviewer can evaluate in
minutes: polished docs, a real scripted demo against the live stack, and a
case-study writeup. No new platform capability this stage. In addition,
close the specific housekeeping items left open by Stage 9's verification
pass and one new one found while preparing this prompt — so the finished
repo has no dangling inconsistency for a reviewer to trip over.

## Decided before this stage (do not re-litigate)

**Housekeeping — resolved this stage, not left open:**

1. **`docs/adr/README.md` is missing its ADR 0020 row.** The file
   `docs/adr/0020-reliability-load-chaos-resilience-slos.md` exists and is
   referenced from `CLAUDE.md`/`PROJECT_STATUS.md`/`README.md`, but the ADR
   index table stops at 0019. Add the missing row, matching the existing
   table's format exactly (title, status, stage).
2. **`pyyaml` is an undeclared direct dependency.** It's imported directly by
   `tests/unit/test_load_profile_compose.py` (Stage 9) but only present
   transitively in `uv.lock` — the same class of latent risk already
   documented once for `sniffio`. Add it to `pyproject.toml`'s explicit
   dependencies at the currently-locked version, re-lock, confirm nothing
   else moves.
3. **The Locust pool-tuning gap gets fixed, not just documented.** Every
   simulated user in `tests/load/locustfile.py` currently authenticates as
   the same single `test-principal`, so they collide on one rate-limit
   bucket almost immediately, and the chat payload never carries a
   `conversation_id` or triggers `document_search` — so Postgres's
   conversation-cache path and Qdrant are never actually exercised under
   load, and Stage 9's pool defaults are still unverified guesses for two of
   three pools. This is a real capability gap in the reliability story this
   portfolio is presenting, and the fix is small and already scoped: mint
   multiple principals (`scripts/generate_api_key.py`, called for N distinct
   keys), have a subset of simulated users carry a stable `conversation_id`
   per user, and have a subset of chat payloads phrased to trigger
   `document_search`. Re-run the primary (cost-free) Locust mode against the
   `test`-profile stack with the fixed harness and record what the pool
   settings actually do under real contention this time. If the observed
   numbers suggest different pool defaults than Stage 9 shipped, change them
   and say why; if they confirm Stage 9's defaults, say that too. Either
   outcome is acceptable — an unexamined harness gap is not.
4. **The `request_id: null` on the Postgres-down 500 (chaos scenario 1)
   stays deferred, explicitly.** Root-causing it isn't worth this stage's
   scope (likely pre-existing, predates Stage 9). Restate it as a known,
   accepted issue in the case-study writeup rather than silently dropping
   it.
5. **The malformed `ANTHROPIC_API_KEY` in `.env` and the
   `docs/architecture-history/generate.py` re-run are operator actions, not
   CC's.** Both need to happen before any demo step that requires a real
   model call. If they haven't happened by the time this stage starts, say
   so plainly in the self-report and note exactly which demo steps had to
   fall back to the `test`-profile stack's `ScriptedLLMClient` instead of a
   real Anthropic call — do not fabricate or imply a real call happened.

**Scope boundaries for this stage:**

- No new ADR describing new platform behavior. If the Locust harness fix
  above needs a decision recorded, add it as a short dated addendum to ADR
  0020 (it already owns the load-testing tool and its two-mode split) —
  don't open a new ADR for what's a fix to an existing decision's
  implementation, not a new decision.
- **The case-study and demo docs get a styled HTML render, same pattern as
  `architecture.html`, not a new one.** This is doc tooling, not platform
  capability — it never touches `services/`, `shared/`, or anything that
  ships in the API image, so it doesn't conflict with "no new platform
  capability" above. Reuse ADR 0010's decision (no CDN, no JS, pre-rendered,
  works offline) rather than re-litigating it or inventing a second styling
  approach.
- Demos use the real stack. No staged or hand-edited screenshots, no
  fabricated terminal output. Container boot + curl, same standing rule as
  every prior stage.
- The case-study writeup has one audience: an ML/AI platform engineering
  hiring reviewer assessing production rigor. Same voice as
  `PROJECT_STATUS.md`/`CLAUDE.md` — factual, evidence-linked (cite the ADR,
  the test, the log line), states what's real vs. deliberately deferred, no
  marketing language, no inflated claims. If something is a known
  limitation, say so as plainly as `PROJECT_STATUS.md` already does.
- Do not re-verify or re-narrate prior stages' claims as if checking them
  again — cite their existing verification logs instead of restating their
  content.

## Scope

1. **Housekeeping fixes** — items 1–3 under "Decided before this stage,"
   with actual command output captured (the `uv.lock` re-solve, the fixed
   Locust run's real numbers).

2. **README.md final pass** — banner updated to "Stage 10 of 10 complete,"
   roadmap table's Stage 10 row filled in (summary/verification links,
   status), stack table and repo-structure block checked against actual
   current source (not copy-pasted from Stage 9's version unchanged).

3. **Demo walkthrough** — `docs/demo.md` as the source of truth (same
   pattern as `docs/architecture.md`), capturing real, actual output against
   the running stack for:
   - A normal authenticated chat call and its response shape.
   - A streaming (`"stream": true`) call showing SSE frames.
   - A call that triggers `document_search` and returns `citations`.
   - An unauthenticated call returning the uniform `401`.
   - Enough rate-limited calls to trigger a real `429`.
   - The circuit breaker, if practically demonstrable this session (e.g.
     against a deliberately wrong endpoint) — otherwise, explicitly note it
     falls back to citing Stage 9's hermetic fault-injection test results
     instead, same fallback Stage 9's own prompt allowed.
   Real captured output only, not paraphrased. Rendered to `demo.html` at
   the repo root via the shared doc-renderer (item 5 below).

4. **Case-study writeup** — `docs/case-study.md` as the source of truth:
   what this platform is and demonstrates, the sequence of production
   concerns added stage by stage, the handful of decisions most worth a
   reviewer's attention (why LangChain was rejected, the hermetic-seam
   pattern reused across Anthropic/Voyage/OTel, the fail-open rate limiter,
   validate-never-apply Terraform), and an honest "what's deliberately not
   built" section pulling from `PROJECT_STATUS.md`'s existing deferred lists
   — told as a narrative through the ADR trail, not a restatement of
   `PROJECT_STATUS.md`'s prose. Rendered to `case-study.html` at the repo
   root via the shared doc-renderer (item 5 below).

5. **Shared HTML doc-renderer** — factor `scripts/build_architecture.py`'s
   markdown-to-HTML machinery (the `_CSS` block, the `markdown-it` render
   call, dark-mode support) out into a small shared module (e.g.
   `scripts/doc_render.py`) so `architecture.html`, `case-study.html`, and
   `demo.html` share one styling source instead of duplicated CSS.
   `build_architecture.py` keeps its Mermaid-specific logic (diagram
   hashing, `docs/diagrams/`, `--render`); drive `case-study.md →
   case-study.html` and `demo.md → demo.html` through the shared renderer
   via a new small script (e.g. `scripts/build_docs.py`) or by extending
   `build_architecture.py` to accept multiple source/output pairs — your
   call, state which and why. No CDN, no JS — same ADR 0010 pattern. Add a
   `--check` drift guard for both new pages, mirroring
   `tests/unit/test_architecture.py`, so an edited `.md` with a stale `.html`
   fails CI the same way. This is doc tooling, not new platform capability —
   it touches only `scripts/`, `docs/`, and the two new root-level `.html`
   files, nothing under `services/` or `shared/`.

6. **Architecture doc / diagram final pass** — confirm `docs/architecture.md`
   and its regenerated `architecture.html` reflect true final state end to
   end; add or adjust one summary diagram if the existing set doesn't
   already give a reviewer a single "whole system" view, otherwise state
   explicitly that the existing set already covers it and why no new
   diagram was added.

7. **All six close-out files** per `docs/contributing.md` → "Ending a
   stage," same as every prior stage: `docs/stage-summaries/stage-10-portfolio.md`,
   `docs/PROJECT_STATUS.md` (stage 10 complete, 10/10, no "next milestone"
   row), `CLAUDE.md`, `docs/architecture.md` + regenerated `architecture.html`,
   `README.md`, `docs/verification-log/stage-10-portfolio.md` (written after
   independent verification, not by CC).

## Required conventions (do not deviate)

- mypy strict, ruff clean, ruff-format clean — same standing gate.
- Container boot in both `prod` and `test` profiles before self-report,
  same standing rule — a green `pytest` does not prove the container boots.
- Never commit secrets; never log PII, tokens, keys, or retrieved excerpts —
  same standing rule, applies to any new demo-output capture too (redact
  any real key material before it goes in a doc).
- `docs/architecture.md` is source; `architecture.html` is generated by
  `scripts/build_architecture.py` — never hand-edited, state the exact
  regenerate command run.
- Same rule extends to the two new pages: `docs/case-study.md`/`docs/demo.md`
  are source, `case-study.html`/`demo.html` are generated — never
  hand-edited, state the exact regenerate command(s) run.
- `CLAUDE.md` stays under 150 lines — state before/after line count.
- ADRs are immutable once accepted — the Locust-fix addendum to ADR 0020 is
  appended, not a rewrite of the original decision.
- Use the canonical stage name `stage-10-portfolio` verbatim everywhere.

## Testing requirements

- Existing test suite (everything from prior stages) still passes
  unmodified; state the new total against the Stage 9 baseline of 395.
- `uv sync` / re-lock after adding `pyyaml` as an explicit dependency —
  confirm no other package version moved.
- Fixed Locust harness: a real primary-mode run (state user count, spawn
  rate, duration) with distinct principals and a `conversation_id`/
  `document_search`-triggering payload mix, with the actual pool-behavior
  numbers observed this time, for all three pools.
- The demo walkthrough's real captured output, per item 3 above.
- New `--check` drift tests for `case-study.html` and `demo.html`
  (mirroring `test_architecture.py`), both passing.
- Full quality gate re-run (`scripts/verify.ps1` / `.sh`) after all fixes,
  with actual output.

## Explicit constraints

- Do not commit or push. Build, test, self-report only — commit happens
  only after independent manual verification, as a separate step, same as
  every prior stage.
- Do not implement any new platform capability. If something looks like it
  needs new application logic beyond the three housekeeping fixes above,
  stop and flag it rather than building it.
- Do not fabricate or imply a real Anthropic call succeeded if the `.env`
  key issue wasn't actually fixed by the time this stage runs — state
  plainly which demo steps used the `test`-profile stack instead.
- Do not open a new ADR for the Locust harness fix — append a dated
  addendum to ADR 0020 instead.
- Do not silently drop the `request_id: null` known issue — it must appear
  in the case-study writeup as an accepted, deferred item.
- Do not duplicate the CSS/markdown-render logic across scripts — factor it
  into the shared module so `architecture.html`, `case-study.html`, and
  `demo.html` stay visually consistent and never drift stylistically from
  each other.

## Self-report requirements

Write `docs/stage-summaries/stage-10-portfolio.md` containing:

- Confirmation of each housekeeping fix (ADR index row, `pyyaml` dependency,
  Locust harness), with actual command output.
- The fixed Locust run's real numbers and whatever pool-default decision
  followed from them (changed or confirmed, with the observed reason
  either way).
- Whether the `.env` key and `architecture-history` regeneration had been
  done by the operator before this stage started, and which demo steps (if
  any) had to fall back to the `test`-profile stack as a result.
- The demo walkthrough's location and its actual captured output for each
  of the six items in Scope §3.
- `docs/case-study.md`'s location and a one-paragraph summary of its
  contents.
- `CLAUDE.md` line count before/after.
- Confirmation `docs/architecture.md` was checked/updated and
  `scripts/build_architecture.py` was re-run, with the exact command.
- Confirmation `case-study.html` and `demo.html` were generated via the
  shared doc-renderer, the exact build command(s) run, and that the new
  drift-guard tests pass.
- Confirmation of the full quality gate (test count vs. the 395 baseline,
  ruff/mypy/format results).
- Confirmation all six close-out files were updated, listed explicitly.
- Any deviations from this scope and why.
- Known limitations carried forward into the final writeup (the
  `request_id: null` issue, anything else still genuinely open) — restate
  plainly, do not bury.

State only what was directly run and observed this stage. Do not restate
prior-stage claims as if re-verified — cite their existing verification
logs instead.
