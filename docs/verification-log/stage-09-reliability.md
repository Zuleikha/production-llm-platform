# Stage 9 — Reliability — Verification Log

Independent verification, separate from Claude Code's self-report at
`docs/stage-summaries/stage-09-reliability.md`. Performed by zulu (commands run
directly on the real machine in Git Bash, results pasted back and cross-checked
against source by Claude, step by step, waiting for real output before
advancing), checked against the running stack and the actual source, not
against the self-report's claims alone. Two rounds of real bugs were found
during this pass, sent back to Claude Code to fix, and re-verified — this log
covers the whole arc, not just the final clean state.

**Date:** 2026-07-24
**Stage:** 9 — Reliability

## Result: PASS, with three real defects found and fixed during verification, and known limitations carried forward honestly rather than hidden

The self-report was unusually candid up front: Docker was down for CC's entire
build session, so it explicitly marked the container-boot check, both Locust
modes, and the chaos runbook as "not run" rather than fabricating results. This
verification pass is what actually ran all of those for the first time, and it
found three real bugs the self-report's own honesty had left undiscovered —
all three are now fixed, tested, and re-verified.

## Discrepancies found and resolved

**1. `docker-compose.yml` hardcoded `ENVIRONMENT: dev` (real defect).** Stage
9's own `tests/load/README.md` instructs `ENVIRONMENT=test docker compose up`
for the cost-free primary Locust mode. That override was silently ignored —
the `api` service's `ENVIRONMENT` was a literal, not `${VAR:-default}` like
every other env-derived value in the file. The `test` profile could never
actually be reached via docker compose. Fixed:
`ENVIRONMENT: ${ENVIRONMENT:-dev}`. Confirmed via `docker compose config`
before and after.

**2. Unhandled 500s leaked a raw Python traceback in the `dev` profile (real
defect, found while probing bug 1 with a deliberately invalid
`ANTHROPIC_API_KEY`).** `create_app` wired `debug=settings.debug` into
FastAPI, and `dev.env` sets `DEBUG=true`. Starlette's `ServerErrorMiddleware`
under `debug=True` renders a raw traceback and bypasses the registered
catch-all `Exception` handler — so `anthropic.AuthenticationError` (correctly
not tripping the circuit breaker, since it's not in `PROVIDER_DOWN_ERRORS`)
reached the client as a full stack trace, violating CLAUDE.md's "500s never
leak internals" rule. Fixed: `create_app` now hardcodes `debug=False`;
`settings.debug` still drives uvicorn `--reload` only. New regression test
(`test_non_qualifying_provider_exception_renders_uniform_500_envelope`)
proven both ways — fails on the exact leak with the old code, passes with the
fix. Independently reproduced live: a request through the rebuilt `dev`
container with a deliberately malformed key returned the clean
`internal_error` envelope, no traceback, after the fix (it had returned a full
traceback before it).

**3. The `test`-profile container authenticated with `dev` keys, not
`test.env`'s fixed key (real defect, deeper than #1, found running the actual
primary Locust mode for the first time).** `docker-compose.yml` interpolates
`API_KEYS`/`ANTHROPIC_API_KEY`/`VOYAGE_API_KEY`/`API_KEY_HASH_SECRET` from root
`.env` at the **compose layer**, before the app's own "test profile ignores
root `.env`" logic ever runs — so even with bug #1 fixed and `ENVIRONMENT=test`
correctly selected, the container's `API_KEYS` still came from root `.env`'s
dev principal, and every Locust chat request 401'd. Fixed with a new
`docker-compose.test.yml` override using **literal** values (the only form
that wins over both the shell and root `.env` — the null/passthrough form and
`--env-file` were both tried and empirically rejected via `docker compose
config`, see ADR 0020 addendum 2). Guarded by a new hermetic drift test
(`tests/unit/test_load_profile_compose.py`) plus a documented manual runtime
check. Independently reproduced live: `docker compose -f docker-compose.yml -f
docker-compose.test.yml up` → `/health` reports `"environment":"test"` →
`Authorization: Bearer test-key-not-a-real-secret` → **200**.

All three fixes were reviewed against the actual diff (not just the self-report
text) before being accepted, and the full quality gate was independently
re-run after each round.

## Automated gate — independently re-run after every fix round, not just accepted from the self-report

| Checkpoint | `pytest` | `ruff check` | `ruff format --check` | `mypy` (strict) |
|---|---|---|---|---|
| Before any fixes (self-report's own claim) | 391 passed, 9 skipped | clean | 84 files | clean, self-report said 84 files |
| Independently re-run, same state | **391 passed, 9 skipped** ✓ | clean ✓ | 84 files ✓ | clean — **82 files**, not 84 |
| After bug 1 + 2 fix | 392 passed, 9 skipped (self-report) | clean | 84 files (self-report) | clean, self-report said 84 files |
| Independently re-run | **392 passed, 9 skipped** ✓ | clean ✓ | 84 files ✓ | clean — **82 files** ✓ (consistent with the row above) |
| After bug 3 fix | 395 passed, 9 skipped (self-report) | clean | — | clean, self-report said 85 files |
| Independently re-run | **395 passed, 9 skipped** ✓ | clean ✓ (chained `&&`, so a prior-step failure would have stopped it) | clean ✓ | clean ✓ (exact file count not re-split out, chain confirmed overall pass) |

**Minor, non-blocking, recurring imprecision:** the self-report's mypy file
count (84, then 85) never matched what actually ran locally (82, consistently,
across two independent checks). Root cause identified and accepted: mypy's
`files =` setting in `pyproject.toml` is an explicit whitelist
(`services`, `shared`, `tests`, `scripts`, minus `tests/load/`), while
`ruff format --check .` scans the whole non-gitignored tree — the two tools
were never scanning the same file set, so the same number was never guaranteed
for both. Not a functional defect (mypy reports clean either way); just a
self-report accuracy note for future stages.

## Structural checks

| Check | Result |
|---|---|
| `wc -l CLAUDE.md` | **150** — at the cap, matches self-report. |
| `git status --short` | Matches the self-report's declared file list **exactly** — every modified/new file accounted for, nothing extra, nothing missing, across all three fix rounds. |
| Secret grep (manual substitute for gitleaks, which wasn't on this machine's `PATH` this session) across every new/changed Stage 9 file | Clean — no `sk-ant-`, `pa-<value>`, or AWS-shaped key patterns. |
| `uv run python scripts/build_architecture.py --check` | `architecture.html is up to date.` |
| `uv sync --frozen` | 139 packages, same as the Stage 8 baseline — no new locked dependency needed. |

## Behavioral verification (live stack, both profiles, not just code review)

**`dev` profile:** `docker compose up -d --build` → `/health`
`{"environment":"dev"}`, `/ready` all three stores `ok`, `/version` correct,
unauthenticated chat → `401`. An authenticated call could not be completed with
the real Anthropic API this session — the operator's own `.env` has a
malformed `ANTHROPIC_API_KEY` (`ssk-ant-api03-…`, a doubled leading character,
confirmed present in the file itself) unrelated to any Stage 9 code. This
surfaced bug #2 (the traceback leak) and is otherwise tracked as separate
housekeeping, not a code defect.

**`test` profile (after bug 1 + 3 fixes):** `docker compose -f
docker-compose.yml -f docker-compose.test.yml up -d --build` → `/health`
`{"environment":"test"}`, all three stores `ok`, and
`Authorization: Bearer test-key-not-a-real-secret` → **200** with zero
host-side setup, as Mode 1 was always supposed to work.

**Primary Locust run (Mode 1, cost-free, 50 users / 5 spawn-rate / 3 min):**
Ran twice — once before bug 3's fix (100% of `chat`/`chat-stream` requests
401'd, which is what led to finding bug 3), and once after (requests correctly
reached the app and were rate-limited, not rejected on auth). **Proved:** the
rate limiter enforces its threshold correctly under real concurrent HTTP load,
not just a single curl (one of the two reliability surfaces this stage's
prompt explicitly said it owns). **Did not prove:** pool tuning for
Postgres/Qdrant — the locustfile's every simulated user authenticates as the
same single `test-principal`, so (a) they collide on one rate-limit bucket
almost immediately, capping realistic throughput, and (b) the request body
carries no `conversation_id` and never triggers `document_search`, so neither
Postgres's conversation-cache path nor Qdrant is actually exercised by this
harness regardless of concurrency. Pool defaults remain at Stage 5's original
values (10/10/10), unchanged, same as the self-report already stated — this
verification pass confirms *why* that data is still unobtainable with the
harness as built, rather than just noting it's missing.

**Chaos runbook**, run against the `test`-profile stack under real background
Locust load (30 users: 15 `ChatUser` + 15 `ProbeUser`):

- **Scenario 1 (Postgres down)** — `/ready` → `503`, names `postgres`;
  stateless chat still succeeds (`200`); chat with a `conversation_id` fails
  loudly (`500`, no traceback — confirms bug 2's fix holds on this path too,
  not just the case it was written for); one minor side-observation, not
  blocking: the `500`'s `request_id` came back `null` instead of a real id,
  likely pre-existing (Postgres + conversation history predates Stage 9) and
  not chased further this pass. Recovery: `/ready` back to `200` after
  `start postgres`, no api restart.
- **Scenario 2 (Redis down)** — `/ready` → `503`, names `redis`; chat still
  succeeds (`200`, rate limiter fails open as designed, ADR 0008/0019);
  `ratelimit.degraded` logged every time; `rate_limiter_fail_open_total`
  increments. **Real finding, not a bug:** under actual concurrent load the
  fail-open path's underlying exception was frequently
  `redis.exceptions.MaxConnectionsError` (pool exhaustion from every
  concurrent request holding a slot while its connect times out), not a plain
  connection-refused — exactly the texture a single curl would never surface,
  and exactly why the runbook insists on running scenarios under load.
  Fail-open held correctly every time regardless. Recovery: `/ready` back to
  `200` within ~2s of `start redis`, no api restart.
- **Scenario 3 (Qdrant down)** — `/ready` → `503`, names `qdrant`; chat still
  answers with `citations: []` (no `document_search`). **Real finding, not a
  bug:** the first probe under load returned `429`, not `200` — a artifact of
  every simulated user and the manual probe sharing the same single
  `test-principal`'s rate-limit bucket, unrelated to Qdrant. After the load
  quiesced, the expected `200`/`citations: []` was confirmed. Recovery:
  `/ready` back to `200` within ~4s of `start qdrant`, no api restart. Caveat
  correctly noted: the `test` profile's scripted client never actually
  *requests* `document_search`, so this proves the endpoint survives losing
  Qdrant, not a live retrieval-skip under a real model (that needs Mode 2).
- **Scenario 4 (Anthropic circuit breaker)** — not live-tested (the real
  provider is deliberately never taken down); covered by the hermetic
  fault-injection tests reviewed directly against source
  (`tests/unit/test_resilience.py`, `tests/unit/test_llm.py`) — confirmed
  these genuinely drive closed → open → half-open → closed with a
  hand-cranked clock and a fault-injecting double, not just asserted to.

Final state after all scenarios: `/ready` `200`, all stores `ok`, Locust
stopped, nothing left running that shouldn't be.

## Non-blocking / known gaps carried forward

- **Pool tuning for Postgres/Qdrant is still not data-driven** — the
  locustfile needs multiple minted principals (to avoid one shared rate-limit
  bucket) and a payload that actually exercises `conversation_id`/
  `document_search` before a real sweep can produce numbers. Tracked as an
  open item, not attempted further this pass.
- **gitleaks / pip-audit were not independently re-run locally** this
  session (binary not on this machine's `PATH`) — CI still enforces both; a
  manual grep substitute was used instead and came back clean.
- **Mode 2 (the billable, real-API Locust run) and the live prompt-cache-hit
  check were not run** — no billable call was authorised this session, and
  the operator's `.env` `ANTHROPIC_API_KEY` is currently malformed regardless
  (separate housekeeping, tracked outside this stage).
- **Per-conversation concurrency control and LLM-based summarization** remain
  out of scope by design, per the stage prompt's own explicit decision —
  restated here, not re-litigated.
- **A `null` `request_id` on the Postgres-down 500** (Scenario 1) — minor,
  likely pre-existing, not chased down this pass.
- **`pyyaml`, imported directly by the new drift-guard test, is not declared
  as an explicit dependency** anywhere in `pyproject.toml` — present only
  transitively. Tests currently pass; flagged as the same class of latent risk
  the project already documented once for `sniffio`.

## Sign-off

Stage 9 is verified complete. Three real defects were found by actually
running the stack for the first time (Docker had been down for the entire
build session) — all three fixed, covered by new hermetic tests, and
independently re-verified against the real diff and a re-run gate, not
accepted on self-report text alone. The chaos runbook and both Locust modes
that the self-report had honestly marked "not run" were run for real this
pass. Remaining gaps are genuine open items, not corners cut, and are carried
forward explicitly rather than hidden. Nothing blocks committing Stage 9's
work.
