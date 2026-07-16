# Stage 4 — RAG — Verification Log

Independent verification, separate from Claude Code's self-report at
`docs/stage-summaries/stage-04-rag.md`. Performed by zulu (commands run
directly on the real machine, results reported back and cross-checked
against source by Claude), checked against the running stack and the
actual source/diff, not against the summary's claims alone.

**Date:** 2026-07-16
**Stage:** 4 — RAG
**Verified against:** uncommitted working tree, HEAD at `f9c55e8` (Stage 3's
final commit). Nothing has been committed or pushed for Stage 4 — confirmed
below.

## Result: PASS

Every checkable claim in the self-report was independently corroborated —
package pins, `PROJECT_STATUS.md` wording, all five automated gates, both
container-boot profiles, the fixture corpus, and the live datastore state.
One anomaly surfaced during verification and was resolved as a false alarm
on the verifier's (Claude's) side, not a defect in Stage 4's work — see
below. No real discrepancy was found.

## Anomaly investigated and resolved

**CLAUDE.md line count.** Claude's sandbox-mounted copy of the repo read
140 lines for `CLAUDE.md` against the self-report's claimed 149 (before and
after, net zero). Before treating this as a defect, it was checked against
the actual machine: `wc -l CLAUDE.md` on zulu's real filesystem returned
**149**, matching the self-report exactly. `git show HEAD:CLAUDE.md | wc -l`
independently confirmed the pre-stage-4 committed baseline was also 149,
consistent with the report's claimed net-zero change achieved through
tightening. Conclusion: Claude's sandbox mount was stale/out of sync with
the real repo at the moment of the first check — a tooling artifact on the
verifier's side, not a false claim in the self-report. All subsequent
checks in this log were run by zulu directly on the real machine, not
through Claude's sandbox, to avoid repeating this.

## Environment / tooling

| Check | Result |
|---|---|
| `wc -l CLAUDE.md` | 149 lines — matches self-report's claimed 149 → 149 exactly. |
| `git status --short` | Matches the self-report's described footprint exactly: known modified files (config, services, tests, docs) plus new ADRs 0011–0015, new `services/retrieval/*` modules, new test files, new stage-04 prompt/summary. Nothing staged. |
| `git log --oneline -3` | `HEAD` still at `f9c55e8` (Stage 3's final commit), equal to `origin/master` — confirms nothing committed or pushed. |
| Secret grep (`sk-ant-*`, `pa-*` patterns across `.py`/`.md`/`.toml`/`.yml`) | Clean. Zero matches. |
| `.env` never tracked/committed | `git status --short .env` and `git log --all --oneline -- .env` both empty. |
| `uv run ruff check .` | `All checks passed!` — matches. |
| `uv run ruff format --check .` | `64 files already formatted` — matches. |
| `uv run mypy` | `Success: no issues found in 63 source files` — matches. |
| `uv run pytest` | `246 passed, 9 skipped, 2 warnings` in 8.38s. Test count matches exactly. Skip breakdown matches exactly: 3 live-Postgres/Redis + 4 live-Qdrant + 2 live-provider-contract = 9. |
| `uv run python scripts/build_architecture.py --check` | `architecture.html is up to date.` — matches. |

**Warning-count note (non-issue).** The self-report claimed "1 warning";
the real run showed 2 — the pre-existing Starlette/`httpx` deprecation
(claimed) plus a `.pytest_cache` permission-denied `PytestCacheWarning`
(not claimed). The second is CLAUDE.md's own documented Windows-only quirk
("carried forward, not investigated"). CC most likely ran its own suite in
a non-Windows-native shell where that warning doesn't fire. Environment
difference, not a wrong claim.

## Static / structural checks

| Check | Result |
|---|---|
| Package pins in `pyproject.toml` | `llama-index-core==0.14.23`, `voyageai==0.5.0`, `qdrant-client==1.18.0` — all three match the self-report exactly. |
| `docs/PROJECT_STATUS.md` row 85 | Reads `_pending independent verification_` verbatim, exactly as the self-report described (not a fabricated link). |
| ADRs 0011–0015 | All five present as untracked files, alongside the existing 0001–0010 and a README index. |
| Stub folders (`services/evaluation`, `services/monitoring`, `services/security`) | No files newer than `docs/stage-summaries/stage-03-agents.md` — genuinely untouched this stage. |
| Fixture corpus (`data/corpus/`) | All 4 files present as claimed: `code-review.md`, `deployments.md`, `incident-response.md`, `observability.md`. |

## Behavioral verification (live stack, not just code review)

**Datastores CC left running**, confirmed via `docker ps`: `postgres:16-alpine`
and `redis:7-alpine` both `Up ... (healthy)`; `qdrant/qdrant:v1.18.1` up —
the version matches the compose-image bump claimed in ADR 0012 exactly
(`v1.12.4` → `v1.18.1`).

**Fresh image build**, independent of CC's own build:

```
docker build -t plp/api:verify .
```

Succeeded, exported and named cleanly (mostly cache-hit, expected given no
source changed since CC's build — confirms reproducibility, not a rebuild
artifact).

**Test-profile boot**, a new container (`plp-verify-test`) separate from
CC's, port 8020:

```
GET  /health   → {"status":"ok",...,"environment":"test"}
GET  /ready    → {"status":"ready","checks":{"postgres":"not_configured","redis":"not_configured","qdrant":"not_configured"}}
POST /v1/chat/completions {"messages":[{"role":"user","content":"verify stage 4"}]}
  → {..., "usage":{"prompt_tokens":3,"completion_tokens":5,"total_tokens":8}, "citations":[]}
```

Matches the self-report exactly, including the new top-level `citations`
field on the response.

**Prod-profile boot**, a new container (`plp-verify-prod`) on the real
compose network, real datastore URLs, placeholder (non-real) API keys —
deliberately not curling `/v1/chat/completions` here, since that would
attempt a real paid Anthropic/Voyage call with a fake key:

```
GET  /health → {"status":"ok",...,"environment":"prod"}
GET  /ready  → {"status":"ready","checks":{"postgres":"ok","redis":"ok","qdrant":"ok"}}
```

All three stores `ok` — matches the self-report's claim that the retrieval
tool is correctly wired against live datastores in the prod profile.

Both verification containers (`plp-verify-test`, `plp-verify-prod`) were
removed after checking; CC's original datastore containers were left
running, untouched.

## Non-blocking / known gaps (disclosed by Claude Code, not found during verification)

- **The live-Qdrant integration tests (4) and the live-provider contract
  test (2 real API calls) were not independently re-run this verification
  pass.** Accepted on the self-report's word, same treatment Stage 3 gave
  its own live Anthropic calls. The self-report's captured output (Qdrant
  point counts, vector dimensions, cosine similarity values, Anthropic
  `usage=in:28/out:5`) is internally consistent with the rest of the
  verified state (collection exists, dimension matches `VOYAGE_EMBEDDING_DIMENSIONS=1024`
  from `.env.example`) but was not re-executed live here. Cross-check your
  Anthropic/Voyage billing consoles if independent cost confirmation is
  wanted.
- **The in-container ingestion CLI was not independently re-run.** Accepted
  on the self-report's word.
- **CI was not run** — nothing has been pushed yet (matches "not committed,
  not pushed" throughout).
- **`verify.ps1`/`verify.sh` were not run as a single script** — the
  constituent commands were run individually instead, same pattern as
  Stage 3's verification.
- `StarletteDeprecationWarning` and the Windows `.pytest_cache`
  permission-denied warning — both carried forward, unchanged, non-blocking.
- The two known, deliberately-scoped limitations the self-report itself
  flagged (upsert-only ingestion orphaning shrunk documents' tail chunks;
  prompt-injection mitigation is delimiting, not immunity) were not
  re-litigated here — they're disclosed design decisions with ADR pointers
  (0012, 0014), not gaps to verify.

## Sign-off

Stage 4 is verified complete. Every checkable claim in the self-report —
package versions, `PROJECT_STATUS.md` wording, the full quality gate
(ruff, ruff format, mypy, pytest, architecture drift check), both
container-boot profiles against live datastores, and the fixture corpus —
was independently corroborated on zulu's own machine, not just reviewed as
text. One anomaly (a stale sandbox mount on Claude's side producing a false
CLAUDE.md line-count mismatch) was investigated and resolved as a
non-issue before it could be mistaken for a defect in Stage 4's work. No
real discrepancy was found. Remaining gaps (live-provider tests and the
ingestion CLI not independently re-executed, CI not yet run) are accepted
deferrals consistent with how Stage 3 was signed off, not concealed
shortcuts. Clear to commit.
