# Stage 3 — Agents — Verification Log

Independent verification, separate from Claude Code's self-report at
`docs/stage-summaries/stage-03-agents.md`. Performed by zulu (commands run
directly; results reviewed and cross-checked against source by Claude),
checked against the running stack and the actual source/diff, not against
the summary's claims alone.

**Date:** 2026-07-15
**Stage:** 3 — Agents
**Commit verified:** `20eb977`

Verification was performed against the uncommitted working tree (HEAD then at
`8bc012a`, Stage 2's final commit); that tree became `20eb977`. Two deltas
between what was verified and what was committed, recorded here rather than
left implicit:

- **`README.md` was refreshed to Stage 3 *after* this log was written** (log
  16:27, README edits 16:33) and is therefore in `20eb977` but was **not**
  independently verified. Docs-only: it corrects a status banner stale since
  Stage 1, moves `langgraph`/`anthropic` into the Runtime stack row, marks
  `agents`/`orchestrator` implemented, updates the roadmap, and fixes a wrong
  Grafana port (3000 → 3001). No code, test, or gate change.
- **`CHECKPOINT.md` was deleted before the commit**, as anticipated below.

Everything else — all source, tests, ADRs and the five gates checked above — is
unchanged between the verified tree and `20eb977`.

## Result: PASS

All checklist items verified. No discrepancy found between the self-report
and observed behavior this stage — contrast with Stage 2, where independent
verification caught a real `asyncpg` install defect. One anomaly was
investigated and resolved as a non-issue (see below). Known gaps are the
ones Claude Code itself disclosed as not-yet-done, not gaps found during
verification.

## Environment / tooling

| Check | Result |
|---|---|
| `uv sync` | `Resolved 135 packages` / `Checked 73 packages` — count mismatch investigated, see below. No install failure. |
| `uv run pytest -v` | 185 passed, 3 skipped, 188 collected. Matches self-report exactly. |
| `uv run ruff check .` | All checks passed. |
| `uv run ruff format --check .` | 54 files already formatted. |
| `uv run mypy` | Success: no issues found in 53 source files. |
| `uv run python scripts/build_architecture.py --check` | architecture.html is up to date. |
| `wc -l CLAUDE.md` | 149 lines — matches self-report's claimed 150 → 149. |
| `git status` | Clean scope, nothing staged, nothing stray beyond expected footprint. |

## Anomaly investigated and resolved

**`uv sync` reported `Resolved 135 packages` but `Checked 73 packages`.**
Given Stage 2's known cross-drive hardlink silent-install bug (packages stay
in `uv.lock` but never land in the venv), this mismatch was investigated
before trusting it. `grep -n "link-mode" pyproject.toml` confirmed
`link-mode = "copy"` is still intact at line 93 — the Stage 2 fix was not
dropped despite `pyproject.toml` being modified this stage. The full test
suite (188 collected) and `mypy` (53 source files) both ran clean with zero
`ModuleNotFoundError` or collection errors, which would be the direct
symptom if packages were actually missing. Conclusion: the 135-vs-73
figures reflect `uv`'s internal accounting (resolved set vs. packages
needing a dependency-marker check), not a broken install. Not a recurrence
of the Stage 2 defect.

## Behavioral verification (not just code review)

**Test-count correction claim.** The self-report states the real "before"
count was 63, not the 56 recorded in prior docs, and that the docs figure
was already stale by the time Stage 3 started. Verified directly: raw
`def test_` counts at the two relevant commits — 47 at `9e25850` (Stage 2's
verified commit) vs. 54 at `8bc012a` (true pre-Stage-3 HEAD, two docs-only
commits later). That's a +7 delta, exactly matching the claimed 56 → 63
correction. The correction is genuine, not a convenient excuse — two
docs-only commits after Stage 2's sign-off quietly added tests before Stage
3 began.

**Circular import bug and fix.** Self-report claims
`services/orchestrator/base.py` importing `services.api.schemas` created a
real circular import, fixed by giving the orchestrator its own `Turn` type.
Verified: `services/orchestrator/conversations.py` carries an explicit
docstring stating the `Turn` type is deliberately not
`services.api.schemas.ChatMessage`, "in fact created an import cycle."
Two regression tests exist in `tests/unit/test_architecture.py`:
`test_the_orchestrator_can_be_imported_without_the_api` (subprocess-based,
reproduces the actual crash scenario) and
`test_the_orchestrator_does_not_import_the_api` (static layering check).
This is a properly defended fix, not a patch over the symptom.

**Postgres persistence**, tested against the live Docker stack (containers
left running from CC's session, confirmed `Up 2 hours (healthy)` for both
postgres and redis):

```
docker exec production-llm-platform-postgres-1 psql -U platform -d platform -c "\dt" -c "SELECT * FROM schema_migrations;"

 conversation_messages | table
 conversations         | table
 schema_migrations     | table

 version |     name      |          applied_at
       1 | conversations | 2026-07-15 13:24:16.37908+00
```

Matches the self-report's claim exactly.

**Live-datastore integration test**, run against the same live containers
rather than fakes:

```
TEST_DATABASE_URL=postgresql://platform:platform@localhost:5432/platform TEST_REDIS_URL=redis://localhost:6379/0 \
  uv run pytest tests/unit/test_integration_agent.py -v
→ 9 passed
```

Matches the self-report exactly (all 3 previously-skipped tests now run
and pass with real dependencies).

## Static / structural checks

| Check | Result |
|---|---|
| Secret grep (`services/`, `shared/`, `tests/`, `*.py`) | Clean. Every match is an obvious fake test key (`sk-ant-not-a-real-key`, `sk-ant-x`, etc.); `test_config.py:135` explicitly asserts no real-key pattern leaks into committed files. |
| Stub check on `services/{retrieval,evaluation,monitoring,security}` | Structure unchanged from Stage 2 baseline — `README.md` + `base.py` + `__init__.py` only, `retrieval/base.py` still the largest (56 lines). None of the four appear in `git status`'s modified list — genuinely untouched, not touched-and-reverted. |
| ADRs 0006–0010 | All five present as untracked files. Spot-checked 0006 and 0008 in full: both carry the complete Context / Decision / Rejected alternatives / Consequences structure, not stubs. |
| `git status` diff scope | Modified list matches Stage 3's actual footprint exactly: `services/agents` and `services/orchestrator` promoted from stub (their `README.md`/`__init__.py` changed), plus `.env.example`, `docker-compose.yml`, `docs/PROJECT_STATUS.md`, `docs/adr/README.md` all updated as required. Untracked list matches expected new files (5 ADRs, `migrations/`, new orchestrator/agent modules, 6 new test files, `docs/diagrams/`, stage-03 prompt + self-report). `CHECKPOINT.md` present as expected — a session-handoff scratch file (same pattern as Stage 2's), to be deleted before commit. Nothing staged, correctly following "do not commit before verification." |
| Docker stack | `postgres` and `redis` up and healthy (2hr uptime, matches the self-report's "left running" note). No `api`, `qdrant`, `prometheus`, or `grafana` containers running. |

## Non-blocking / known gaps (disclosed by Claude Code, not found during verification)

- The `api` container was never built or booted this stage — the chat
  endpoint has been exercised only in-process via tests, never end-to-end
  through a running container with real HTTP + curl. Deferred to before or
  shortly after commit, at zulu's discretion.
- CI was not run this stage (no push yet — commit hasn't happened).
- `verify.ps1` was not run.
- The `prod` profile was never started or tested this stage.
- The two real Anthropic API calls (~$0.021 total, referenced in the
  self-report as confirming model ID, usage field names, streaming event
  mapping, and tool_use→tool_result replay) are unverifiable from the repo
  itself — no repo-side artifact records them. Accepted on the self-report's
  word; cross-check your Anthropic console billing if independent
  confirmation is wanted.
- `StarletteDeprecationWarning` (`httpx`/`TestClient`, suggests `httpx2`)
  and the Windows `.pytest_cache` permission-denied warning — both
  carried forward unchanged from prior stages, non-blocking.

## Sign-off

Stage 3 is verified complete. Every checkable claim in the self-report was
independently corroborated against source, git history, and the live
Docker stack — the test-count correction, the circular-import fix, the
Postgres schema, and all five automated gates (ruff, ruff format, mypy,
full pytest, architecture drift check) matched exactly. One anomaly (uv's
135-vs-73 package count) was investigated and resolved as non-blocking. No
real defect was found this stage. Remaining gaps (API container not
booted, CI not run) are honestly disclosed deferrals, not concealed
shortcuts. Clear to commit.
