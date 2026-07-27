# Stage 10 — Portfolio — Verification Log

Independent verification, separate from Claude Code's self-report at
`docs/stage-summaries/stage-10-portfolio.md`. Performed by zulu (commands run
directly on the real machine in Git Bash, results pasted back and cross-checked
against source by Claude, step by step, waiting for real output before
advancing), checked against the running stack and the actual source, not
against the self-report's claims alone. Two real defects were found during
this pass — one in `README.md`, one in `scripts/demo.sh` — both fixed and
re-verified before sign-off.

**Date:** 2026-07-27
**Stage:** 10 — Portfolio

## Result: PASS, with two real defects found and fixed during verification, and all known limitations carried forward honestly rather than hidden

## Discrepancies found and resolved

**1. `README.md` overclaimed independent verification (real defect, found on
first read of the self-report, before any hands-on check).** The banner said
*"All ten stages are built and independently verified"* and the roadmap table
marked Stage 10 `✅ current — complete`, linking to
`docs/verification-log/stage-10-portfolio.md` — which did not exist yet at
that point. Stage 9's own process explicitly flagged this exact failure mode
(README shipped stale once already) as a recurring risk. Fixed directly:
banner changed to `🚧 Stage 10 of 10 built — pending independent
verification`, the intro paragraph now states Stages 1–9 are independently
verified and Stage 10 is self-reported pending its own, and the roadmap
table's verification column says `pending` instead of linking to a file that
didn't exist. This file's existence, on sign-off, is what makes those claims
true again.

**2. `scripts/demo.sh` step 4 produced a parse error instead of the
unauthenticated `401` body (real defect, found running the actual script).**
`curl -o - -w '\nHTTP %{http_code}\n' | pp` sent the status-code suffix to the
same stdout stream as the JSON body, immediately before piping to `pp`
(`json.tool`) — so the parser received the body followed by a stray
`HTTP 401` line and failed with `Extra data: line 2 column 1 (char 124)`.
Every other step either has no `-w` (steps 1–3) or doesn't pipe it through
`pp` (step 5), so this was the only step affected. Fixed: capture body and
status together in one `curl` call (`resp=$(curl -sS -w $'\n%{http_code}' ...)`),
split them with bash parameter expansion (`${resp%$'\n'*}` for the body,
`${resp##*$'\n'}` for the code), pretty-print only the body, print the status
separately. Independently reproduced live: `docs/demo.md`'s step 4 now shows
the clean parsed `401` envelope followed by `HTTP 401` on its own line, no
"Extra data" error; `bash -n scripts/demo.sh` parses clean.

Both fixes reviewed against the actual diff (not just the self-report text)
before being accepted.

## The `.env` operator loose ends — closed during this session, not before it

Both items flagged in the Stage 9 → Stage 10 handoff as still-open operator
housekeeping were fixed mid-verification, live, with real output captured:

- **`ANTHROPIC_API_KEY`** was still the malformed `ssk-ant-...` key Stage 9
  found (doubled leading character) — confirmed present, unfixed, at the
  start of this session. Corrected to `sk-ant-...`, verified two ways: a raw
  call directly to `api.anthropic.com` returned `not_found_error` on an
  unrelated bad model name (proving auth succeeded, not `authentication_error`),
  and a real authenticated call through the running `dev`-profile container
  returned genuine model content (`"Hi"`, real token usage 1088/5/1093).
- **`docs/architecture-history/generate.py`** re-run confirmed Stage 9's tab
  now reads from its real commit (`e4eb3e409`) instead of the working-tree
  fallback it had snapshotted mid-build; Stage 10's tab correctly still shows
  `working-tree (uncommitted — finalizes at commit)`, as documented.

Because the key was fixed mid-session, CC re-captured the model-dependent
demo steps for real against the `dev` profile (chat, streaming, and — after
an explicit go-ahead for the small, billable Voyage ingestion cost — a real
non-empty 4-entry `citations` array). This is recorded as a dev-profile
addendum in `docs/demo.md`, clearly separated from the cost-free `test`-profile
echo captures used for the reproducible default run.

## Automated gate — independently re-run, not just accepted from the self-report

| Checkpoint | `pytest` | `ruff` | `ruff format` | `mypy` (strict) | `gitleaks` | `pip-audit` |
|---|---|---|---|---|---|---|
| Self-report's claim | 405 passed, 9 skipped | clean | 88 files | clean, 86 source files | no leaks | no vulnerabilities |
| Independently re-run (`./scripts/verify.sh`) | **405 passed, 9 skipped** ✓ | clean ✓ | 88 files ✓ | clean, 86 source files ✓ | no leaks ✓ | no vulnerabilities ✓ |

Exact match, no discrepancy. All 9 skips are the standing opt-in layers
(live Postgres/Redis/Qdrant, live-contract tests) — nothing new skipped.

*Note: this reviewer's own sandbox could not independently re-run the suite a
second time end-to-end — it has only Python 3.10 and no network path to
download 3.12 (the project pins `>=3.12,<3.13`). The table above is zulu's
real run on the actual machine, cross-checked by Claude against the self-report,
not a second independent execution by Claude.*

## Structural checks

| Check | Result |
|---|---|
| `wc -l CLAUDE.md` | **149** — under the 150 cap, matches the self-report's stated 150→149. |
| `git log` / `git status` | HEAD still at Stage 9's `e4eb3e4` — **no commit made**, as required. 27 changed/new files, consistent with the stage's scope. |
| ADR 0020 index row | Present in `docs/adr/README.md`, format matches Stage 9's row exactly. |
| ADR 0020 addenda 2 & 3 | Both present in `docs/adr/0020-...md` — addendum 3 (2026-07-27) matches the self-report's Locust-fix numbers exactly. |
| `pyproject.toml` | `pyyaml==6.0.3` present as an explicit dependency. |
| `docs/PROJECT_STATUS.md` | Stage 10 marked complete, 10/10, no next-milestone row. |
| Locust principal mirroring | `config/environments/test.env` and `docker-compose.test.yml` both carry exactly 20 `loaduser-0`…`loaduser-19` hash entries — confirmed by direct extraction, not just a line count (an initial naive grep count of 21 was this reviewer's own regex artifact matching a comment's `loaduser-{i}` placeholder, not a real extra principal). |
| `docs/case-study.md`, `docs/demo.md` | Both substantive, every claim ties to a real, checkable ADR — not templated filler. |
| New request-lifecycle diagram | `docs/diagrams/flowchart-td-7cecf4f646fa.svg` exists, 120 KB — a real render, not a stub. |
| `tests/unit/test_docs_render.py` | 6 test functions, several parametrized over 2 pages → 10 actual test cases at collection time, matching the self-report's "10 tests" claim. |
| `tests/unit/test_security.py` edit | Legitimate contract fix (asserts principal count against the real parsed key store, not a hardcoded number invalidated by the new load principals) — not a test weakened to force a pass. |

## Behavioral verification (live stack, both profiles, not just code review)

**`test` profile:** `docker compose -f docker-compose.yml -f docker-compose.test.yml up -d --build` → `/health` `{"environment":"test"}`, `/ready` all three stores `ok`.

**`prod` profile:** `ENVIRONMENT=prod docker compose up -d --force-recreate api` → `/health` `{"environment":"prod"}`, `/ready` all three stores `ok`, unauthenticated chat → `401` (auth wired, no model call attempted). Stack restored to the `test`-profile Mode 1 state afterward, confirmed via `/health` showing `"environment":"test"` again.

**`scripts/demo.sh` full run** (after the step-4 fix): steps 1–3 show real `test`-profile echo behavior with `citations: []` as documented for Mode 1; step 4 now shows the clean parsed `401` envelope; step 5 showed 57×`200` then 13×`429` on one run (self-report's own example run showed 60/10 — same mechanism, ordinary run-to-run timing variance, not a discrepancy); step 6 correctly falls back to the hermetic fault-injection tests.

**Locust Mode 1 re-run** (60 users = 30 `ChatUser` + 30 `ProbeUser`, spawn-rate 10, 2 min, against the live `test`-profile stack):

| | Self-report | This verification run |
|---|---|---|
| chat failures | 353/1580 (22.3%), all `429` | 250/1562 (16.0%), all `429` |
| chat-stream failures | 28/155 (18%), all `429` | 20/141 (14.2%), all `429` |
| health/ready failures | 0 | 0 |
| chat p99 | 140 ms | 140 ms |
| max latency | 602 ms | 435 ms |

Different exact counts (expected — real concurrent HTTP load, not deterministic), same shape: only `429`s as failures, health/ready untouched, sub-150 ms p99. Server-side log check during the run (`docker compose logs api --since 5m | grep -i "error\|traceback\|pool"`) returned **empty** — zero errors, tracebacks, or pool-related log lines, matching the self-report's "0 ERROR-level lines, 0 pool timeouts" claim. This independently backs the "pool defaults confirmed, not changed" decision in ADR 0020 addendum 3.

## Non-blocking / known gaps carried forward

- **Locust Mode 2 (billable, real-API run) was not re-run** this pass, same as the self-report — no additional billable spend authorized beyond the citations-ingestion cost already approved mid-session.
- **`request_id: null` on the Postgres-down 500** — restated as accepted/deferred in `docs/case-study.md`, `CLAUDE.md`, `docs/PROJECT_STATUS.md`. Not chased down, as agreed.
- **Per-conversation concurrency control and Voyage circuit breaking** remain out of scope by standing decision, restated accurately, not re-litigated.
- This reviewer's sandbox could not independently re-execute the Python quality gate a second time (no Python 3.12, no network path to install it) — the quality-gate table above reflects zulu's real run, cross-checked against the self-report and the actual diffs, not a second independent execution.

## Sign-off

Stage 10 is verified complete. Two real defects were found by actually reading
the diff critically and running the real stack — a premature `README.md`
verification claim (fixed before any further check, to avoid compounding the
exact mistake Stage 9 already flagged as a recurring risk) and a genuine shell
bug in `scripts/demo.sh` step 4 (fixed and re-verified). The two operator loose
ends carried from the Stage 9 handoff — the malformed Anthropic key and the
stale architecture-history snapshot — were both closed live during this
session, with real captured evidence, not just asserted. The full quality
gate, both container profiles, the demo script end-to-end, and a live Locust
re-run all independently confirm the self-report's claims within normal
run-to-run variance. Remaining gaps are genuine, previously-agreed open items,
not corners cut, and are carried forward explicitly. All ten stages of
`production-llm-platform` are now built and independently verified. Nothing
blocks CC from committing Stage 10's work.
