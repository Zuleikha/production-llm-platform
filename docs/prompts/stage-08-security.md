# Stage 8 — Security

## Objective

Implement the `services/security` contract for real: API-key authentication on
the data-bearing endpoint, Redis-backed rate limiting, input/output guardrails
extending ADR 0014's retrieved-text mitigation into a real second layer, and two
new hermetic CI gates (secret scanning, dependency vulnerability scanning). The
API is unauthenticated today (`docs/architecture.md`'s "Deliberate non-goals");
this stage removes that, deliberately scoped — not full enterprise IAM.

## Decided before this stage (do not re-litigate)

- **Auth mechanism: API key, bearer-style, not JWT.** `Authorization: Bearer
  <key>`. Keys are static, issued out-of-band, and mapped to a principal id.
  No issuer, no expiry, no external IdP — this project has none, and building
  JWT issuing/rotation machinery is its own scope, not a one-stage add-on.
  Store only a salted hash of each key (never the raw value), compare with
  `hmac.compare_digest`, never log the credential itself (existing PII/secret
  logging rule, `docs/coding-standards.md`).
- **Which endpoints require auth.** Only `POST /v1/chat/completions`.
  `/health` and `/ready` **stay unauthenticated** — kubelet's liveness/readiness
  probes (wired in Stage 7's Helm chart) cannot supply a credential, and gating
  them would break every pod's probe path. `/version` and `/metrics` also stay
  unauthenticated, matching how Prometheus scrapes today (no credential path
  configured for it) — the same call ops teams make for metrics endpoints
  behind network-level trust rather than app-level auth. State this boundary
  explicitly in the ADR; do not add auth to any of these three by accident
  while wiring the middleware.
- **AuthZ: single-tier, not role/scope-based.** A valid key is a valid
  principal with full access to the chat endpoint. No admin/user split this
  stage — the API has exactly one data-bearing endpoint family, and inventing
  a second capability tier just to exercise RBAC would be scope creep, not
  hardening. Document this as a deliberate boundary to revisit if a second
  capability tier (e.g. an admin/ops endpoint) is ever added.
- **Rate limiting: Redis-backed, not in-memory.** Keyed by authenticated
  principal id. Stage 7 already ships configurable replica counts and an
  optional HPA — an in-memory counter would silently under-count the moment
  there is more than one pod, which this project's own Kubernetes work makes
  a real bug, not a hypothetical one. Reuse the existing
  `DatastoreRegistry.redis_client` seam (`shared/datastores.py`); do not open a
  second Redis connection path. Implement the increment+check as a single
  atomic operation (Lua script or `INCR`+`EXPIRE` pipeline) — a
  read-then-write is a race across replicas and must not ship.
  **Fail-open on Redis unavailability**, matching the precedent already
  accepted for the conversation cache (ADR 0008: a Redis failure degrades
  service rather than fails loud) — but the degraded state must be logged as
  an event every time it happens, not silently absorbed. A rate limiter that
  fails closed would turn a cache outage into a full outage of the paid LLM
  endpoint, which is a worse failure mode than the rate limit briefly not
  applying.
- **RAG injection hardening: two concrete additions on top of ADR 0014, not a
  replacement for it.** The nonce-fencing mechanism in
  `services/retrieval/tool.py` stays exactly as it is.
  1. **A heuristic secondary screen over retrieved excerpts** — a `Guardrail`
     implementation that flags excerpts matching known injection patterns
     ("ignore previous instructions", tool-invocation-shaped text, etc.) as a
     **logged signal**, not a silent drop. ADR 0014 already rejected a
     blocklist as the *primary* control because it's trivially evaded and
     mangles legitimate security-discussion documents — this stage's addition
     is framed the same honest way: defense-in-depth telemetry, not a claim of
     detection completeness.
  2. **An egress check on the model's final answer** — before the response
     reaches the client, check whether the answer text leaks the per-call
     nonce/fence markers or reproduces the untrusted-data preamble verbatim.
     Either is a concrete signal that something unexpected happened during
     generation. Log it; do not silently rewrite the answer.
  **Explicitly deferred, do not build this stage:** per-source trust tiering.
  ADR 0014 ties that to the corpus admission model changing (user uploads, a
  crawl, third-party feeds) — none of that has happened; the corpus is still
  committer-only. Building trust tiers against an unchanged threat model is
  premature infrastructure. State this deferral in the new ADR, matching how
  ADR 0014 itself named it as future work.
- **Guardrail on the user's own chat input, distinct from the retrieval
  screen.** Before the agent loop runs, screen the incoming user message with
  the same `Guardrail` ABC (a second implementation, not a special case) for
  obvious jailbreak/PII patterns. Log a flagged event always. Only block
  outright for cases judged egregious enough to justify a 4xx — err toward
  logging-not-blocking where the call is close, and say which cases you chose
  to block and why in the self-report. A block must return the standard error
  envelope, never a silent empty completion.
- **Secret management scope: CI scanning, not a cloud secrets backend.** Two
  new hermetic CI jobs — a secret-scanning gate over the repo (pick one
  well-known tool, e.g. `gitleaks`; state which and why) and a dependency
  vulnerability scan (`pip-audit` against `uv.lock`). Neither touches
  Terraform's Secrets Manager references from Stage 7 — those remain
  validated-never-applied (ADR 0018), and this stage does not add a runtime
  secrets-backend integration on top of the existing env-var precedence
  (ADR 0003). That would need a real AWS account this project doesn't have.

Write one ADR (0019, continuing sequentially) covering: the API-key AuthN
choice and the three exempted endpoints, the single-tier AuthZ boundary, the
Redis-backed rate limiter and its fail-open behavior, the two RAG-hardening
additions plus the explicit trust-tiering deferral, and the CI-scanning-only
secret-management scope.

## Scope

1. **`services/security/base.py` and a new `services/security/` implementation
   module** — concrete `AuthProvider` (API-key based) and at least two
   `Guardrail` implementations (retrieved-excerpt injection screen, user-input
   screen; the egress check may be a third `Guardrail` or a dedicated function
   — your call, state which and why). Remove the stub language from
   `services/security/README.md` and `__init__.py`'s docstring; the ABCs in
   `base.py` stay as the contract, concrete classes go alongside them.

2. **Auth wiring into the API** — a FastAPI dependency (preferred, to match
   `EngineDep`'s existing pattern in `services/api/routes/chat.py`) or
   middleware enforcing bearer auth on `/v1/chat/completions` only. Missing or
   invalid credential → `401` via the existing uniform error envelope
   (`services/api/errors.py`); do not reveal in the response body *why* it
   failed (wrong key vs malformed header vs missing header are all the same
   response shape).

3. **Rate limiting** — Redis-backed, keyed by principal id, applied to
   `/v1/chat/completions` after auth resolves the principal. Configurable
   threshold via `Settings` (e.g. requests per window). Exceeding it → `429`
   via the same uniform envelope. Fail-open + logged on Redis unavailability,
   per the decision above.

4. **RAG injection hardening additions** in `services/retrieval/tool.py` (or a
   sibling module if that keeps the file from growing unreasonably — your
   call): the excerpt-screen `Guardrail` and the answer-egress check. Existing
   ADR 0014 tests must keep passing unmodified — this stage adds a layer, it
   does not touch the nonce mechanism itself.

5. **User-input guardrail** wired in before the agent loop starts (likely in
   `services/api/completions.py` or the chat route, wherever the request first
   reaches engine code — state where and why).

6. **`shared/config.py` additions** — new `Settings` fields for: the API key
   store (format is your call — e.g. a JSON/CSV env var of `principal:hash`
   pairs; state the shape and why), rate-limit threshold(s), and any guardrail
   toggles. Extend `_require_secrets_and_urls_in_prod` so `prod` refuses to
   boot without the key material configured, matching how it already refuses
   to boot without the datastore URLs and API keys. No new hermetic-seam
   pattern is needed here (unlike `build_llm_client`/`build_embeddings_client`)
   — auth, rate limiting, and guardrails are all local logic with no paid
   external hop, so `test` can construct all of them for real.

7. **CI additions** — two new required, hermetic jobs in
   `.github/workflows/ci.yml`: secret scanning and dependency vulnerability
   scanning. State the exact tool versions pinned and the exact commands.

8. **Tests** — mirror source under `tests/unit/services/security/`, extend
   `tests/unit/services/api/` for the auth dependency and rate limiter, extend
   `tests/unit/services/retrieval/` for the two new guardrail checks. Cover at
   minimum: valid key, invalid key, missing header, malformed header (all → the
   same 401 shape); rate limit under/at/over threshold; rate limiter behavior
   when Redis is unavailable (fail-open, logged); an excerpt containing an
   injection-pattern string is flagged; a clean excerpt is not; an answer that
   echoes the nonce/preamble is flagged; one that doesn't, isn't.

9. **Docs** — ADR 0019; `services/security/README.md` updated from "planned,
   not yet implemented" to what's actually built (matching how Stage 7 updated
   the K8s/Terraform READMEs); `docs/architecture.md` new "Security" section,
   regenerated; `CLAUDE.md` current-state + deferred-table update; `docs/PROJECT_STATUS.md` stage-8 marked complete.

## Required conventions (do not deviate)

- One ADR (0019, continuing sequentially) — the combined decision from above.
- `docs/PROJECT_STATUS.md` updated at end of stage — mark stage 8 complete,
  leave other stages' rows untouched.
- `CLAUDE.md` stays under 150 lines. State before/after line count. Update the
  "Deferred" table — Stage 8's row is done, remove it; Stage 9's row stays.
  Also update the **Layout** section's `services/security/` line — it
  currently reads "⬜ stub"; replace with a one-line description of what's
  actually implemented, matching the style of the `services/agents/`,
  `services/retrieval/`, etc. lines above it.
- `docs/architecture.md` updated (new Security section: what's authenticated,
  what isn't and why, rate-limiting shape, the two RAG-hardening additions);
  regenerate via `uv run python scripts/build_architecture.py` and state the
  exact command. Never hand-edit `architecture.html`.
- No hardcoded secrets anywhere, including test fixtures — API keys used in
  tests must be obviously fake (e.g. a fixed test-only value), never a
  plausible-looking real key. The existing secret grep/test coverage extends
  to these new files.
- Never log a raw API key, a raw user message, or raw retrieved excerpt text
  in a guardrail-flagged event — log the event, the principal id, and a
  boolean/score, exactly the existing "events not sentences, never log
  attacker-influenced content" rule already applied to `retrieval.tool.py`.
- `@traced` on new application functions (the auth dependency, rate limiter,
  guardrail checks), same exemptions as always (`shared/logging.py`,
  `shared/observability.py`, lifespan/async generators).
- mypy strict, ruff clean, tests mirror source, write the failing test first.
- **Container boot is required, not deferrable** (standing CLAUDE.md rule).
  Build and run in both `prod` and `test` profiles; curl `/v1/chat/completions`
  without a key (expect 401), with a valid key (expect it to proceed), past
  the rate limit (expect 429), and confirm `/health`/`/ready` still work with
  **no** credential.

## Testing requirements

- New unit tests for auth, rate limiting, both guardrail additions, all
  passing; state the new total test count against the Stage 7 baseline of 311.
- `ruff check .`, `ruff format --check .`, `mypy` (strict) all clean.
- Secret-scan CI job clean (or documented false positives, following the same
  precedent as Stage 7's `manage_master_user_password` grep false-positive).
- Dependency-scan CI job clean (or documented accepted findings with
  justification — do not silently suppress a real finding).
- The real container-boot check from the section above, with actual curl
  output captured (status codes and bodies), not paraphrased.
- Existing test suite (ADR 0014's nonce-fencing tests, everything from prior
  stages) still passes unmodified.

## Explicit constraints

- Do not commit or push. Build, test, self-report only — commit happens only
  after independent manual verification, as a separate step.
- Do not implement JWT, OAuth2, or any external identity provider — API keys
  only, per the decision above.
- Do not add role/scope-based AuthZ or any admin-only endpoint invented just
  to exercise it.
- Do not attempt a real cloud secrets-manager integration — CI scanning only,
  no AWS credentials, no changes to the Terraform secrets module's
  validate-never-apply boundary (ADR 0018).
- Do not weaken, remove, or bypass ADR 0014's existing nonce-fencing mechanism
  — this stage adds a layer on top of it.
- Do not silently drop or rewrite guardrail-flagged content without logging
  the event first — fail-loud philosophy applies to guardrails too, just
  scoped to "log and optionally reject," not "silently sanitize."
- Do not gate `/health`, `/ready`, `/version`, or `/metrics` behind auth.
- Use the canonical stage name `stage-08-security` verbatim everywhere.

## Self-report requirements

Write `docs/stage-summaries/stage-08-security.md` containing:

- What was implemented, file by file
- `CLAUDE.md` line count before/after, and confirmation the Stage 8
  deferred-table row was removed
- The exact API-key format/storage decision made and why
- The exact rate-limiting algorithm implemented (atomic operation used) and
  confirmation of fail-open behavior on a simulated Redis outage
- What the two RAG-hardening additions actually detect, with a concrete
  example excerpt/answer that trips each one and one that doesn't
- Which user-input cases were chosen to hard-block vs log-only, and why
- The exact container-boot commands and their actual curl output (401 without
  a key, success with one, 429 past the rate limit, `/health`/`/ready`
  unauthenticated and reachable)
- Confirmation of the full Python quality gate (test count vs the 311
  baseline, ruff/mypy/format results)
- The exact secret-scan and dependency-scan tool versions, commands, and
  actual output
- Confirmation `docs/architecture.md` was updated and
  `scripts/build_architecture.py` was re-run, with the exact command
- Any deviations from this scope and why
- Known limitations or deliberately deferred items (JWT/external IdP,
  role/scope AuthZ, per-source trust tiering, cloud secrets-manager
  integration) — restate why, even though each is already agreed above

State only what was directly run and observed this stage. Do not restate prior
-stage claims as if re-verified.
