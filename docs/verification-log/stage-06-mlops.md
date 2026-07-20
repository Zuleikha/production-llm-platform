# Stage 6 — MLOps — Verification Log

Independent verification, separate from Claude Code's self-report at
`docs/stage-summaries/stage-06-mlops.md`. Performed by zulu (commands run
directly on the real machine in Git Bash, results pasted back and
cross-checked against source by Claude, step by step, waiting for real
output before advancing), checked against the running stack and the actual
source, not against the summary's claims alone.

**Date:** 2026-07-20
**Stage:** 6 — MLOps
**Verified against:** uncommitted working tree, HEAD at `bf5fcaa` (Stage 5's
final commit). Nothing has been committed or pushed for Stage 6 — confirmed
below.

## Result: PASS

Every checkable claim in the self-report was independently corroborated —
the eval regression gate, the full quality gate, both container-boot
profiles, and every structural claim (files, ADR, doc fixes). No
discrepancies found this stage.

## Automated gate

| Check | Result |
|---|---|
| `uv run python scripts/evaluate.py` (Tier 1 eval regression gate) | 8/8 cases, `recall_at_k=1.0000`, `mrr=1.0000` — matches baseline (`data/eval/baseline.json`) exactly, both metrics at their 1.0 floor with 0.05 tolerance. `PASS - no regression against baseline.` |
| `./scripts/verify.sh` — `ruff check .` | `All checks passed!` |
| `./scripts/verify.sh` — `ruff format --check .` | `73 files already formatted` — matches. |
| `./scripts/verify.sh` — `mypy` (strict) | `Success: no issues found in 72 source files` — matches. |
| `./scripts/verify.sh` — `pytest` | `311 passed, 9 skipped, 2 warnings` in 7.31s — matches self-report's claimed `273 → 311 (+38)` exactly. Skip breakdown unchanged from prior stages (3 live-Postgres/Redis + 4 live-Qdrant + 2 live-provider-contract = 9). |

Both warnings are the same pre-existing, documented, non-blocking ones
carried since Stage 4 (Starlette/`httpx` deprecation; Windows
`.pytest_cache` permission-denied) — not new to this stage.

## Structural checks

| Check | Result |
|---|---|
| `services/evaluation/` | `base.py`, `baseline.py`, `dataset.py`, `judge.py`, `metrics.py`, `README.md` all present. |
| `data/eval/` | `dataset.json`, `baseline.json`, `README.md` present. `baseline.json` is valid JSON: `mrr: 1.0`, `recall_at_k: 1.0`, `k: 3`, `tolerance: 0.05`, with a documented `notes` field — not placeholder content. |
| `scripts/evaluate.py` | Present. |
| `tests/unit/test_evaluation.py` | Exactly 38 `def test_` functions — matches self-report's claimed count precisely. |
| ADR 0017 | Present at `docs/adr/0017-rag-evaluation-and-regression-gate.md`, indexed in `docs/adr/README.md` row 32 (RAG evaluation: recall@k/MRR, CI-vs-opt-in two-tier split, regression baseline — Accepted, Stage 6). |
| `.gitignore` fix | Confirmed: `!data/eval/` present with an explanatory comment — the claimed necessary fix (without it, `data/*` would have blocked the eval fixtures from ever being committed) actually landed. |
| `CLAUDE.md` | 149 lines, under the ceiling. Deferred-table fix confirmed: "OTel metrics export" now sits under the Stage 9 row, not Stage 6 — matches the scope decision made before this stage was drafted. |
| `docs/PROJECT_STATUS.md` | `Current stage: Stage 6 — MLOps (complete)`, `Overall progress: 6/10 — 60%` — matches. |
| `README.md` | Genuinely updated to "Stage 6 of 10 complete — MLOps," correctly notes metrics deferred to Stage 9 and evaluation scoped to RAG retrieval only. The Stage-4-stale drift CC flagged and fixed is confirmed fixed. |
| CI workflow (`.github/workflows/ci.yml`) | New `eval` job present: "RAG eval regression gate (Tier 1)," runs `uv run python scripts/evaluate.py`, no API key referenced — hermetic, matches the CI-blocking-vs-opt-in design decided before this stage. |
| `services/api/app.py` | Zero references to `evaluate` or `Evaluator` — confirms the eval harness is genuinely not wired into app startup, as required. |
| Nothing committed | `git log --oneline -3` still shows HEAD at `bf5fcaa`, Stage 5's final commit — confirms "not committed" throughout. |

## Behavioral verification (live stack, not just code review)

**Fresh image build**, independent of CC's own build session:

```
docker build -t plp/api:local .
```

Succeeded (mostly cache-hit, expected — no source changed since CC's build).

**Test-profile boot** (`plp-test`, port 8010):

```
GET /health → {"status":"ok",...,"environment":"test"}
GET /ready  → {"status":"ready","checks":{"postgres":"not_configured","redis":"not_configured","qdrant":"not_configured"}}
```

Matches established test-profile behavior, no regression from adding the
eval module.

**Prod-profile boot** (`plp-prod`, port 8011), dummy values for all five
required prod settings — deliberately not curling `/v1/chat/completions`:

```
GET /health → {"status":"ok",...,"environment":"prod"}
GET /ready  → 503 {"status":"not_ready","checks":{"postgres":"unavailable","redis":"unavailable","qdrant":"unavailable"}}
```

Fail-loud as designed, consistent with every prior stage.

Both boot checks hit the same empty-reply-then-retry timing race seen in
prior stages' verification (curl fired before the container fully bound its
port) — not a defect, resolved by retrying a few seconds later both times.

## Non-blocking / known gaps (disclosed by Claude Code, not found during verification)

- **Tier 2 (LLM-as-judge) was not run** — makes real, billable Anthropic API
  calls, requires explicit human confirmation per the standing rule. CC
  correctly did not run it and disclosed this rather than skipping silently.
  Not run during this verification pass either, for the same reason.
- **CI was not run** — nothing has been pushed yet, consistent with "not
  committed, not pushed."
- The full 8-container compose stack (Tempo/collector/Grafana from Stage 5)
  was not re-verified this stage — out of scope for Stage 6, no changes
  claimed to that pipeline.

## Sign-off

Stage 6 is verified complete. Every checkable claim in the self-report — the
eval regression gate's actual output, the full quality gate, both
container-boot profiles, every new file, the ADR, the `.gitignore` fix, and
both doc-drift fixes (`CLAUDE.md` deferred table, stale `README.md`) — was
independently corroborated on the real machine, not just reviewed as text.
No discrepancies found. Clear to commit.
