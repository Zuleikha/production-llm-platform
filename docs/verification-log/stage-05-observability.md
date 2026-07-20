# Stage 5 — Observability — Verification Log

Independent verification, separate from Claude Code's self-report at
`docs/stage-summaries/stage-05-observability.md`. Performed by zulu (commands
run directly on the real machine, results pasted back and cross-checked
against source by Claude, step by step, waiting for real output before
advancing), checked against the running stack and the actual source, not
against the summary's claims alone.

**Date:** 2026-07-18
**Stage:** 5 — Observability
**Verified against:** uncommitted working tree, HEAD at `a3e7a98` (the
`.gitignore` housekeeping commit from before Stage 5 began). Nothing has been
committed or pushed for Stage 5 — confirmed below.

## Result: PASS

Every checkable claim in the self-report was independently corroborated: the
full quality gate, both container-boot profiles, the full 8-container compose
stack, and a real trace observed end-to-end in Tempo. One minor discrepancy
was found and is non-blocking — see below.

## Discrepancy found

**mypy file count.** The self-report claims "no issues, 66 files." The real
run reported `Success: no issues found in 65 source files`. Off by one file.
Not investigated further — doesn't affect the pass/fail result, and no
mypy errors were hidden either way. Worth a glance if it recurs on a future
stage, otherwise not worth chasing.

## Automated gate

Run via `powershell.exe -File scripts/verify.ps1` directly on the real
machine (not `pwsh` — confirmed not installed on this machine, per CC's own
note in the self-report).

| Check | Result |
|---|---|
| `ruff check .` | `All checks passed!` — matches. |
| `ruff format --check .` | `66 files already formatted` — matches. |
| `mypy` (strict) | `Success: no issues found in 65 source files` — self-report claimed 66; see discrepancy above. |
| `pytest` | `273 passed, 9 skipped, 2 warnings` in 8.31s — matches self-report's claimed `246 → 273 (+27, all in test_tracing.py)` exactly. Skip breakdown matches: 3 live-Postgres/Redis + 4 live-Qdrant + 2 live-provider-contract = 9. |

Both warnings are pre-existing, documented, non-blocking (Starlette/`httpx`
deprecation; Windows `.pytest_cache` permission-denied) — carried forward,
not new to this stage.

## Structural checks

| Check | Result |
|---|---|
| `docs/stage-summaries/stage-05-observability.md` | Exists, 353 lines — matches self-report's own stated length. |
| ADR 0016 | Present at `docs/adr/0016-observability-stack.md`, indexed in `docs/adr/README.md` row 31 (Observability stack: OTel traces → Collector → Tempo, Grafana-native alerting — Accepted, Stage 5). |
| `CLAUDE.md` line count | `wc -l` → 149. Matches self-report's claimed `150 → 149`, under the ceiling. |
| `docs/PROJECT_STATUS.md` | `Current stage: Stage 5 — Observability (complete)`, `Overall progress: 5/10 — 50%` — matches. |
| `shared/observability.py` | Both the sync wrapper (lines 115-116) and the async wrapper (lines 86-87) now carry `record_exception=False, set_status_on_exception=False` — confirms the actual root-cause fix described in the self-report (the bug was the sync wrapper missing this, not stale bytecode as first suspected). |
| Nothing committed | `git log --oneline -3` still shows HEAD at `a3e7a98`, the pre-stage housekeeping commit — confirms "not committed" claim throughout. |

## Behavioral verification (live stack, not just code review)

**Fresh image build**, independent of CC's own build session:

```
docker build -t plp/api:local .
```

Succeeded (mostly cache-hit, expected — no source changed since CC's own
build; confirms reproducibility, not a rebuild artifact).

**Test-profile boot** (`plp-test`, port 8010):

```
GET /health → {"status":"ok",...,"environment":"test"}
GET /ready  → {"status":"ready","checks":{"postgres":"not_configured","redis":"not_configured","qdrant":"not_configured"}}
```

Matches Stage 2-4's established test-profile behavior exactly — no
regression from adding tracing.

**Prod-profile boot** (`plp-prod`, port 8011), dummy (non-real) values for
all five required prod settings (`DATABASE_URL`, `REDIS_URL`, `QDRANT_URL`,
`ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`) — deliberately not curling
`/v1/chat/completions`, since that would attempt a real paid call:

```
GET /health → {"status":"ok",...,"environment":"prod"}
GET /ready  → 503 {"status":"not_ready","checks":{"postgres":"unavailable","redis":"unavailable","qdrant":"unavailable"}}
```

Fail-loud as designed — dummy URLs correctly reported `unavailable`, not a
silent pass. Confirms `OTEL_EXPORTER_OTLP_ENDPOINT` is correctly **not** in
the required-settings list — prod booted successfully with no OTel endpoint
set, matching ADR 0016 and `shared/config.py`'s stated design (a trace
backend is not worth a boot failure).

**Full compose stack**, all 8 services:

```
docker compose up -d --build
docker compose ps
```

All 8 containers `Up ... (healthy)` or `Up`: `api`, `postgres`, `redis`,
`qdrant`, `prometheus`, `grafana`, `otel-collector`, `tempo`. Matches the
self-report's claim exactly.

**Real trace confirmed in Tempo** — the critical end-to-end check for this
stage. Generated traffic with five plain `GET /health` requests (deliberately
not the paid chat endpoint — the request middleware traces every request
regardless), then queried Tempo directly:

```
curl -s 'http://localhost:3200/api/search?limit=20'
```

Returned 20 real traces, every one `rootServiceName: "api"`,
`rootTraceName: "RequestContextMiddleware.dispatch"`. This independently
confirms the full pipeline works: `@traced` → OTLP exporter → collector →
Tempo → queryable. Not taken on the self-report's word — observed directly.

## Non-blocking / known gaps (disclosed by Claude Code, not found during verification)

- **Individual trace payload (span attributes, parent/child nesting) was
  not independently inspected** — only the search-list response (trace IDs,
  root service/span name) was checked. The self-report's more detailed claim
  (parent/child nesting with `code.namespace`/`code.function` attributes) was
  not independently re-fetched via Tempo's trace-by-ID endpoint this pass.
- **`verify.ps1` was run as the single script** (unlike Stage 4's
  verification, which ran the constituent commands individually) — no
  material difference expected, noted for consistency.
- **CI was not run** — nothing has been pushed yet, consistent with
  "not committed, not pushed."
- The compose stack was left running per CC's own note, for Grafana/Tempo
  inspection — `docker compose down` still pending, to be run when finished
  looking around.

## Sign-off

Stage 5 is verified complete. Every checkable claim in the self-report — the
full quality gate, the actual root-cause fix in `shared/observability.py`,
both container-boot profiles, the full 8-container compose stack, and a real
trace independently observed in Tempo — was corroborated directly against
the running stack, not just reviewed as text. One minor, non-blocking
discrepancy (mypy's file count off by one) was found and is not a defect.
Clear to commit.
