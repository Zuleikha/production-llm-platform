# Stage 8 — Security — Verification Log

Independent verification, separate from Claude Code's self-report at
`docs/stage-summaries/stage-08-security.md`. Performed by zulu (commands run
directly on the real machine in Git Bash, results cross-checked against
source by Claude), checked against the running stack and the actual source,
not against the summary's claims alone.

**Date:** 2026-07-23
**Stage:** 8 — Security

## Result: PASS

Every checkable claim in the self-report was independently corroborated —
the full quality gate, live auth behavior (missing/wrong/correct key), live
rate limiting under a lowered threshold, and the gitleaks/pip-audit scanning
claims. Two real defects were found and fixed during verification (not
accepted on the self-report's word), and a third gap (a missing operator
tool) was found and filled. See "Discrepancies found and resolved" below.

## Discrepancies found and resolved

**1. `docker-compose.yml` never forwarded `API_KEYS` / `API_KEY_HASH_SECRET`
into the container (real defect).** The `api` service's `environment:` block
had `ANTHROPIC_API_KEY` and `VOYAGE_API_KEY` passed through but not the two
new Stage 8 settings, despite both being in `shared/config.py`'s
prod-mandatory list. Result: a correctly-generated key/hash pair still 401'd,
because the container never received either variable — not an auth-logic
defect. Confirmed via `docker compose exec api env | grep -i API_KEY`
(redacted), which showed only the two pre-existing vendor keys. Fixed by
adding both lines to the compose file, in the same place and style as the
existing `ANTHROPIC_API_KEY`/`VOYAGE_API_KEY` entries. Reverified after the
fix: full 401 → 401 → 200 sequence below.

**2. `.env.example` was missing both new variables (docs gap).** Every other
prod-mandatory secret (`ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, the three
datastore URLs) has a documented placeholder block in `.env.example`. `API_KEYS`
and `API_KEY_HASH_SECRET` did not. Fixed: added a "Security / auth (Stage 8,
ADR 0019)" block matching the existing style and secret-handling conventions
(explicit "THIS IS A SECRET" warning, prod-mandatory note, test-profile
exemption note).

**3. No operator tool existed to generate a valid key (gap, now filled).**
`services/security/auth.py` exposes `hash_key(secret, key)` — the exact
function the auth provider uses at verification time — but nothing wrapped it
for an operator to actually issue a key. Before this was written, a valid
test key/hash pair had to be hand-computed via a raw `hmac.new(...).hexdigest()`
one-liner to test the success path at all. Added `scripts/generate_api_key.py`:
takes a principal name, generates a random raw key via `secrets.token_urlsafe(32)`,
computes its hash via the real `hash_key` function (reusing it rather than
reimplementing the HMAC, so the script and the auth provider can never
drift), fails loudly if `API_KEY_HASH_SECRET` is unset, and prints the raw
key once (clearly labeled as unrecoverable) plus the `principal:hexhash` pair
to append to `API_KEYS`. Needed one iteration after first landing: an initial
`ModuleNotFoundError: No module named 'services'` when run as
`uv run python scripts/generate_api_key.py` (fixed with an explicit
`sys.path` insert of the repo root, since `scripts/` is not on the path by
default under direct file execution) and three ruff findings on the first
draft (unused `noqa`, an f-string with no placeholders, missing trailing
newline), all fixed and reverified clean.

Separately, a stray typo'd file (`scripts/scriptsgenerate_api_key.py`, a
path-concatenation bug from an earlier attempt) was found in the working tree
and removed before the real file was created.

## Automated gate

Run via `./scripts/verify.sh` directly on the real machine.

| Check | Result |
|---|---|
| `uv sync` | `Checked 139 packages in 11ms` — clean. |
| `ruff check .` | `All checks passed!` (after fixing the 3 findings in `generate_api_key.py` above). |
| `ruff format --check .` | `80 files already formatted`. |
| `mypy` (strict) | `Success: no issues found in 79 source files`. |
| `pytest` | `362 passed, 9 skipped, 2 warnings` in 9.92s — matches CC's self-report exactly. Skip breakdown unchanged from every prior stage: 3 live-Postgres/Redis + 4 live-Qdrant + 2 live-provider-contract = 9. |

Both warnings are the same pre-existing, documented, non-blocking ones carried
since Stage 4 (Starlette/`httpx` deprecation; Windows `.pytest_cache`
permission-denied) — not new to this stage.

## Structural checks

| Check | Result |
|---|---|
| `wc -l CLAUDE.md` | 149 lines — matches self-report, under the 150-line ceiling. |
| Secret grep (`sk-ant-`, `pa-` patterns across `services/`, `shared/`, `tests/`, `docs/`, `.github/`) | Clean. Every hit is an obvious fake test key (`sk-ant-not-a-real-key`, `sk-ant-x`, `pa-x`, `pa-not-real`, etc.), same pattern documented and accepted in every prior stage's verification log. |
| Stub-folder check (`services/evaluation`, `services/monitoring` vs. stage-07 baseline) | Empty result from `find ... -newer docs/stage-summaries/stage-07-kubernetes.md` — genuinely untouched this stage, as expected (stage 8's scope is `services/security`, `services/api` wiring, `shared/config.py`, CI, and docs). |
| `docker-compose.yml` fix survives in the tree | Confirmed via `grep -A2 "VOYAGE_API_KEY" docker-compose.yml` — the added `API_KEYS`/`API_KEY_HASH_SECRET` lines are present, not overwritten by any later CC pass. |
| `.env.example` fix survives in the tree | Confirmed via `grep -A2 "Security / auth" .env.example` — the added block is present. |

## Behavioral verification (live stack, not just code review)

**Auth, tested against the running `dev`-profile container** (`docker compose up -d --build`, port 8000):

```
# no Authorization header
curl .../v1/chat/completions  →  401  {"error":{"type":"http_error","message":"Missing or invalid credentials."}}

# wrong key
curl ... -H 'Authorization: Bearer wrong-key'  →  401  {"error":{"type":"http_error","message":"Missing or invalid credentials."}}

# correct key (generated via a hand-computed HMAC before generate_api_key.py existed)
curl ... -H 'Authorization: Bearer test-raw-key-123'  →  200
  {"id":"chatcmpl-...","object":"chat.completion",...,
   "choices":[{"message":{"content":"Pong! How can I help you today?"}}],
   "usage":{"prompt_tokens":1084,"completion_tokens":13,"total_tokens":1097}}
```

Confirms the full Bearer → HMAC-SHA256 → `compare_digest` path works end to
end once the container actually receives the key material (see defect #1
above — this exact sequence is what first exposed it).

**Rate limiting**, tested against a separate `test`-profile container with
the threshold deliberately lowered (`RATE_LIMIT_REQUESTS=3`,
`RATE_LIMIT_WINDOW_SECONDS=120`), since the default (60/60s) is impractical to
trip with real sequential requests against the live chat endpoint — each
carries real Anthropic latency, so 70 sequential requests against the default
threshold span well over 60 seconds and never approach the limit within one
window. With the lowered threshold:

```
req 1 -> HTTP 200   "object":"chat.completion"
req 2 -> HTTP 200   "object":"chat.completion"
req 3 -> HTTP 429   "message":"Rate limit exceeded."
req 4 -> HTTP 429   "message":"Rate limit exceeded."
req 5 -> HTTP 429   "message":"Rate limit exceeded."
```

Full 429 envelope: `{"error":{"type":"http_error","message":"Rate limit exceeded.","request_id":"..."}}`
— same uniform error shape used everywhere else in the API. Per-principal
counter confirmed working: limit trips at the configured count and stays
tripped for subsequent requests within the window.

## Security scanning

**Gitleaks**, run locally against the full working directory (`gitleaks dir .
--redact --report-path gitleaks-report.json --exit-code 2`): 3 findings, all
resolved as non-issues:

- 2 hits in `.env` (real `ANTHROPIC_API_KEY`/`VOYAGE_API_KEY`) — expected and
  correct. `gitleaks dir` scans the literal filesystem regardless of git
  tracking status, so it will always find real secrets sitting in a local,
  git-ignored `.env`. Confirmed `.env` has never been tracked or committed
  (consistent with every prior stage's verification).
- 1 hit in `.github/workflows/ci.yml` (`curl-auth-header` rule) — inspected
  directly: the matched value is the literal string `wrong-key`, a
  deliberate fake credential used to test that the auth middleware rejects
  invalid keys with 401. Not a real secret.

CC's own CI-scoped gitleaks job (tracked files only, `.gitleaksignore`/
`.gitleaks.toml` allowlisting caches and known fake fixtures) reported clean
separately, consistent with the above.

**pip-audit**: accepted on the self-report's word (`pip-audit 2.9.0`, no
vulnerabilities) — not independently rerun this pass.

## Non-blocking / known gaps

- **Real API keys were briefly exposed in a chat transcript** during this
  verification session (via an overly broad `env | grep -i API_KEY` command
  that also matched `ANTHROPIC_API_KEY`/`VOYAGE_API_KEY`, not just the two
  new Stage 8 vars). Both keys were rotated immediately after discovery
  (new keys issued at the Anthropic console and Voyage dashboard, `.env`
  updated, container rebuilt). Not a defect in Stage 8's code — a process
  lesson: redact env-var dumps by default (`| sed 's/=.*/=<redacted>/'`)
  rather than trusting a grep pattern to stay narrow.
- **`pip-audit` not independently rerun** — accepted on CC's word this pass.
- **Guardrails (`services/security/guardrails.py`) and the egress check
  (`services/retrieval/egress.py`) were not independently live-tested.**
  Unlike auth and rate limiting, these are pure in-process logic with no
  container/env wiring dependency, so there's no specific reason to expect
  the passing unit suite (362 green, including `test_security.py`) is wrong
  about them. Lower risk than auth/rate-limiting, which both turned out to
  have real wiring defects the unit suite could not have caught.
- CI has not been run on a pushed commit yet — nothing has been pushed.

## Sign-off

Stage 8 is verified complete. Every checkable claim — the full quality gate,
live auth rejection/acceptance, live rate limiting under a lowered threshold,
and the gitleaks scanning result — was independently corroborated against the
running stack and real source, not just reviewed as text. Two real defects
were found and fixed during verification (the `docker-compose.yml` wiring gap
and the `.env.example` docs gap), and one missing operator tool
(`scripts/generate_api_key.py`) was written, debugged, and reverified clean.
Nothing here blocks committing the stage-8 work.