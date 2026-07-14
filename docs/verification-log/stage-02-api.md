# Stage 2 — API — Verification Log

Independent verification, separate from Claude Code's self-report at
`docs/stage-summaries/stage-02-api.md`. Performed by zulu, checked against
the running stack and the actual source/diff, not against the summary's claims
alone.

**Date:** 2026-07-14
**Stage:** 2 — API
**Commit verified:** 9e25850

## Result: PASS

All checklist items verified. One real defect was found during verification,
root-caused, fixed, and reverified before sign-off — see "Discrepancy found
and resolved" below.

## Environment / tooling

| Check | Result |
|---|---|
| `uv sync` | Initially failed — see discrepancy below. Clean after fix. |
| `uv run pytest -v` | 56/56 passed. Matches claimed count exactly (test_chat.py 15, test_config.py 10, test_datastores.py 13, test_health.py 11, test_logging.py 7). |
| `uv run ruff check .` | All checks passed. |
| `uv run ruff format --check .` | 41 files already formatted. |
| `uv run mypy --strict .` | Success: no issues found in 41 source files. |
| `wc -l CLAUDE.md` | 149 lines — within the 150-line limit. |
| `git status` | Clean scope. CHECKPOINT.md correctly deleted. No stray files. |

## Discrepancy found and resolved

**Local `uv sync` did not install `asyncpg`.** `pytest` failed at conftest
collection with `ModuleNotFoundError: No module named 'asyncpg'`, directly
contradicting the self-report's claim that the full gate passed. Root cause:
a known machine-specific `uv` install bug — uv's default hardlink install
mode fails silently across drives (repo on `D:`, uv cache on `C:`). This is
the same class of issue that had already bitten this project once before
(Stage 1, evidenced against `sniffio` in the repo's own record — not `httpx`,
which was an initial mis-attribution on the verifier's part, corrected by
Claude Code before it was written into CLAUDE.md).

Fix: `[tool.uv] link-mode = "copy"` added to `pyproject.toml`, forcing file
copy instead of hardlink. Verified genuinely effective, not just present —
Claude Code fed the key an invalid value to confirm uv actually parses it
(`error: expected one of clone, reflink, copy, hardlink, symlink`), then
forced a real cross-drive reinstall with `UV_LINK_MODE` unset to confirm the
`pyproject.toml` setting alone (not an environment variable) fixes it.
Reverified independently after the fix: `uv cache clean && uv sync` installs
cleanly, `pytest` collects and passes 56/56.

`CLAUDE.md`'s two previously separate quirk entries for this were merged into
one, since they always shared the same root cause.

## Behavioral verification (not just code review)

**Liveness vs readiness split**, tested against the live Docker stack, not
inferred from code:

```
curl /health          → 200 {"status":"ok",...}
curl /ready            → 200 {"status":"ready","checks":{"postgres":"ok","redis":"ok","qdrant":"ok"}}
docker compose stop redis
curl /ready             → 503 {"status":"not_ready","checks":{...,"redis":"unavailable"}}
curl /health            → 200, unaffected
docker compose start redis
curl /ready              → recovers to 200 "ready"
```

Confirms `/ready` genuinely probes dependencies and `/health` is pure
liveness with no dependency calls, as specified in the stage prompt and
ADR 0005.

**Chat completion + SSE streaming**, tested against the live stack:

- Non-streaming request: correct `ChatCompletionResponse` shape, echo content,
  `finish_reason: "stop"`, `usage` token counts sum correctly.
- Streaming request: multiple discrete `data:` frames (not one blob) — first
  chunk carries `role`, middle chunks carry `content` increments, final chunk
  has empty `delta` with `finish_reason` set, all chunks share one `id`,
  stream terminates with `data: [DONE]`. Matches ADR 0004's documented
  contract exactly.

## Static / structural checks

| Check | Result |
|---|---|
| Secret grep (`services/`, `shared/`, `tests/`) | Clean. Initial run against the whole repo hit false positives in `.venv/site-packages` third-party source — rescoped and reran clean. |
| Stub check on `services/{agents,orchestrator,retrieval,evaluation,monitoring,security}` | All six are genuine stubs — `README.md`, `__init__.py`, `base.py` only, all flagged by the stub-pattern grep. `retrieval/base.py` is larger than the rest (1747 vs 735–1121 bytes); inspected directly — larger because it defines more interface surface (a dataclass + a Protocol + an ABC with two methods), not because anything is implemented. Both abstract methods explicitly `raise NotImplementedError`. |
| ADR 0004 (streaming transport) | Reviewed in full. Documents the SSE decision, rejected alternatives (WebSockets, NDJSON, long polling, gRPC), and the exact chunk framing contract — matches observed curl output exactly. |
| ADR 0005 (datastore pooling/readiness) | Reviewed in full. Documents pool-per-store, concurrent startup (~20s→~7s, matches self-report), the `not_configured`-passes / prod-requires-all-URLs design, and the deliberate choice to keep `qdrant-client` out of base deps in favor of an HTTP probe. Consistent with actual implementation. |
| `tests/unit/test_config.py` diff | `test_prod_profile_switch` updated to supply valid URLs (required by the new prod validator) — a genuine strengthening, not a weakening. New parametrized test `test_prod_refuses_to_start_without_every_datastore_url` explicitly pins fail-loud behavior for each of the three URLs individually. `test_non_prod_profiles_tolerate_absent_datastore_urls` confirms the test profile stays hermetic. |
| `.github/workflows/ci.yml` diff | Adds real postgres/redis/qdrant service containers to the Docker CI job. Qdrant readiness is polled explicitly (30s timeout loop against `/readyz`) before the API container starts — resolves an initial concern about a missing health-check race condition. `/ready` assertion checks each store individually (`postgres:"ok"`, `redis:"ok"`, `qdrant:"ok"`), not just HTTP 200. New CI step curls both the non-streaming and streaming chat endpoints, asserting on `chat.completion.chunk` and `data: [DONE]` — the SSE contract is now gated in CI, not just manually verified once. |
| `httpx` promotion to base deps | Confirmed in `pyproject.toml`, pinned exactly, comment ties it to ADR 0005 (pooled Qdrant HTTP client). Removed from dev group with a corresponding note. |
| `asyncpg` base dep declaration | Confirmed correct and pinned exactly. The install failure was an environment issue (see discrepancy above), not a declaration bug. |

## Non-blocking notes (carried forward, not fixed this stage)

- `StarletteDeprecationWarning`: `httpx` with `starlette.testclient` flagged as
  deprecated, suggesting `httpx2`. Unverified whether this is a current real
  recommendation — worth a look in a later stage, not urgent.
- `PytestCacheWarning: Access is denied` on `.pytest_cache` (Windows). Tests
  still pass; pytest just can't persist its cache. Worth investigating if it
  recurs or starts affecting anything beyond incremental test speed.

## Sign-off

Stage 2 is verified complete. All functional, behavioral, and structural
checks pass. One real defect (asyncpg install failure) was caught during
verification, not accepted on the self-report's word, root-caused to a
recurring machine-specific `uv` issue, and fixed at the `pyproject.toml`
level so it doesn't silently recur for any future contributor on a
different-drive setup.