# ADR 0019 — API-key authentication, single-tier authZ, Redis rate limiting, two RAG-hardening guardrails, and CI-only secret scanning

- **Status:** Accepted
- **Date:** 2026-07-23
- **Stage:** 8 (Security)
- **Builds on:** ADR 0003 (env-var config precedence, no committed secrets), ADR
  0005 (`/health` vs `/ready`, the datastore-URL prod boot requirement), ADR 0008
  (Redis degrades rather than fails loud), ADR 0009/0011 ("nothing in CI spends
  money or needs a real key"), ADR 0014 (retrieved text is untrusted; nonce
  fencing is the primary mitigation), ADR 0018 (Helm secret wiring, Terraform
  validate-not-apply).

## Context

Through Stage 7 the API is unauthenticated: `POST /v1/chat/completions` — which
spends money on every call — is open to anyone who can reach the port, there is
no per-caller rate limit, and the only injection defense is ADR 0014's nonce
fence over retrieved text. Stage 8 closes that gap, deliberately scoped to what
this project actually is (one data-bearing endpoint, no cloud IAM, no identity
provider) rather than a full enterprise security posture. Five decisions are
recorded together because they define one coherent security boundary.

## Decision

### 1. Authentication: static API keys, bearer-style — not JWT/OAuth/IdP

Callers present `Authorization: Bearer <key>`. Keys are static, issued
out-of-band, and mapped to a principal id. No issuer, no expiry, no external IdP:
this project has none, and JWT issuing/rotation machinery is its own scope, not a
one-stage add-on.

**Only a salted hash of each key is stored, never the raw value.** The store is
`principal -> HMAC-SHA256(pepper, key)` hex pairs; on authentication the presented
credential is hashed the same way and compared with `hmac.compare_digest` against
*every* stored hash (constant work regardless of which principal, if any, matches,
so timing does not leak membership). A keyed hash rather than a bare digest means
a leaked store is not a rainbow-table-able list. The raw key never reaches a log,
a committed file, or an exception message (`services/security/auth.py`).

**Config shape.** `API_KEYS` is a comma-separated `principal:hexhash` list (e.g.
`alice:ab12…,bob:cd34…`); `API_KEY_HASH_SECRET` is the pepper. A flat string, not
JSON, so it sets cleanly as one env var with no quoting — the same reasoning the
datastore URLs follow (ADR 0003). A malformed `API_KEYS` entry fails loudly at
parse rather than being silently dropped, so a typo cannot quietly lock everyone
out. Both come from the OS environment only and are **mandatory under `prod`**:
`_require_secrets_and_urls_in_prod` now refuses to boot without them, exactly as
it already does for the datastore URLs and provider keys. `test` constructs the
provider for real from a fixed, obviously-fake key — **no hermetic seam is needed**
(unlike the LLM/embeddings clients) because auth is local logic with no paid
external hop.

**Which endpoints require auth — and the three that deliberately do not.** Only
`/v1/chat/completions` is gated. `/health` and `/ready` stay open because
kubelet's liveness/readiness probes (ADR 0018's Helm chart) carry no credential;
gating them would break every pod's probe path. `/version` and `/metrics` stay
open because that is how Prometheus scrapes today — network-level trust, not
app-level auth. This boundary is stated explicitly so no future middleware wiring
gates them by accident.

### 2. Authorization: single-tier

A valid key is a valid principal with full access to the chat endpoint. No
admin/user or scope split: the API has exactly one data-bearing endpoint family,
and inventing a second capability tier just to exercise RBAC would be scope creep,
not hardening. Revisit if a second capability tier (an admin/ops endpoint) is ever
added.

### 3. Rate limiting: Redis-backed, per-principal, atomic, fail-open

Keyed by authenticated principal id, applied after auth resolves the principal.
**Redis-backed, not in-memory:** Stage 7 ships configurable replicas and an
optional HPA, so an in-memory counter would silently under-count the moment there
is more than one pod — a real bug given this project's own Kubernetes work, not a
hypothetical. It reuses the existing `DatastoreRegistry.redis_client` seam (read at
call time, no second connection path). The increment-and-check is **a single
atomic Lua script** (`INCR`, then `EXPIRE` only when the counter is new) so two
replicas cannot race a read-then-write. Over the limit → `429` in the uniform
envelope; threshold and window are `Settings` fields (`RATE_LIMIT_REQUESTS`,
default 60 / `RATE_LIMIT_WINDOW_SECONDS`, default 60).

**Fail-open on Redis unavailability**, matching the precedent for the conversation
cache (ADR 0008). An unreachable or erroring Redis logs `ratelimit.degraded`
**every time** and allows the request. A limiter that failed closed would turn a
cache outage into a full outage of the paid LLM endpoint — a worse failure mode
than the limit briefly not applying. The degradation is logged, never silently
absorbed.

### 4. RAG injection hardening: two additions on top of ADR 0014, not a replacement

The nonce fence in `services/retrieval/tool.py` stays exactly as it is — it
remains the load-bearing mitigation. Two heuristic layers are added as
**defense-in-depth telemetry, framed the same honest way ADR 0014 framed its own
blocklist rejection: a logged signal, not a claim of detection completeness.**

1. **Excerpt injection screen** (`InjectionPatternGuardrail`, wired into
   `DocumentSearch`): flags retrieved excerpts matching injection-shaped patterns
   (`instruction_override`, `system_prompt_probe`, `tool_invocation`). **Never
   blocks / never drops an excerpt** — the model still needs the document to
   answer questions about it, and a silent drop would both break that and hide the
   signal.
2. **Answer-egress check** (`check_answer_egress`, in the retrieval package to
   avoid a security↔retrieval import cycle and to co-locate it with the fence
   format it references): before the answer reaches the client, flags whether it
   leaks a fence marker (`</?excerpt-[0-9a-f]{8,}` → `nonce_fence_leak`) or echoes
   the untrusted-data preamble verbatim (`preamble_echo`). **Log-only** — never
   rewrites or blocks.

**Per-source trust tiering is explicitly deferred**, exactly as ADR 0014 named it
as future work. It is tied to the corpus admission model changing (user uploads, a
crawl, third-party feeds); none of that has happened — the corpus is still
committer-only — so building trust tiers against an unchanged threat model is
premature infrastructure.

### 5. User-input guardrail: block a narrow egregious set, log the rest

Before the agent loop runs, the current user turn is screened with a second
`Guardrail` implementation (`UserInputGuardrail`). **Blocked (→ `400`, uniform
envelope):** direct `instruction_override` and `system_prompt_probe` — unambiguous
direct attacks. **Logged only:** persona/role-play jailbreaks (`jailbreak_persona`)
and PII-shaped strings (`pii`: email/card/SSN), because a user may legitimately
*discuss* a jailbreak or paste their own email/card, and blocking those would harm
legitimate use. The rule: err toward logging-not-blocking where the call is close.
Every flagged turn is logged (principal + categories, never the text); a block
returns the standard error envelope, never a silent empty completion.

### 6. Secret management: CI scanning only, not a cloud secrets backend

Two new hermetic CI gates: **gitleaks** (`v8.30.1`, pinned) scans the tree for
committed credentials against a repo `.gitleaks.toml` whose allowlist exempts only
the obviously-fake, documented test fixtures (a real key in those files still
trips the default rules); **pip-audit** (`==2.9.0`, pinned) audits the exported,
fully-pinned `uv.lock` against the Python advisory database. Neither touches
Stage 7's Terraform Secrets Manager references (still validate-never-apply, ADR
0018), and this stage adds **no** runtime secrets-backend integration on top of the
existing env-var precedence (ADR 0003) — that needs a real AWS account this project
does not have.

## Consequences

**Positive**

- The paid endpoint is no longer open: every chat call is authenticated and
  rate-limited per principal, with the credential stored only as a salted hash.
- Auth/rate-limit/guardrails are all local logic, so the `test` profile exercises
  them for real — no new hermetic seam, and full HTTP-surface coverage in CI.
- Defense-in-depth over retrieved text without weakening ADR 0014 or over-claiming:
  the fence stays primary, the new layers are honest telemetry.
- Two more hermetic, free, key-free CI gates in the same spirit as the eval and
  container-boot gates — secret leaks and vulnerable dependencies fail the build.

**Negative / accepted limitations**

- **Static keys, no rotation/expiry/revocation beyond editing `API_KEYS` and
  restarting.** No JWT, no OAuth, no external IdP — deferred by decision, not
  oversight.
- **Single-tier authZ:** no admin/ops separation. Revisit only if a second
  capability tier is added.
- **The heuristic guardrails are evadable and will false-positive** — that is why
  the excerpt screen and egress check never block and the user-input block set is
  kept small. They are signal, not a boundary.
- **Rate limiting fails open:** a Redis outage means the limit is briefly not
  enforced. Accepted, logged every time — the alternative (fail closed) is a worse
  outage of the paid endpoint (ADR 0008).
- **Egress check runs after the streamed bytes have left** on the SSE path; it is
  log-only either way, so this is acceptable, but it cannot prevent a leak already
  in flight, only record it.
- **Secret management is scan-only:** no runtime secrets backend. Env-var
  precedence (ADR 0003) and the validate-never-apply Terraform Secrets Manager
  references (ADR 0018) are unchanged.
