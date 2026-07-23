# security — authentication, rate limiting, guardrails

> **Status: implemented in Stage 8 (security).** See ADR 0019 for the decisions
> and their deliberately-scoped boundaries.

## Contract

| Type | Kind | Meaning |
|------|------|---------|
| `AuthProvider` (`base.py`) | ABC | `async authenticate(credential) -> principal_id`; raises `AuthenticationError`. |
| `Guardrail` (`base.py`) | ABC | `async check(text) -> bool` (allow / block). |

## What is built

- **`auth.py` — `ApiKeyAuthProvider`.** Bearer API keys (`Authorization: Bearer
  <key>`), static and issued out-of-band, mapped to a principal id. **Only a
  salted hash is stored** (`HMAC-SHA256(secret, key)`), compared with
  `hmac.compare_digest`; the raw key is never logged or committed. Single-tier
  authZ: a valid key has full access to the chat endpoint (no role/scope split).
- **`rate_limit.py` — `RedisRateLimiter`.** Per-principal fixed-window limiting
  over the shared `DatastoreRegistry.redis_client` seam, using an atomic
  `INCR`+`EXPIRE` Lua script. **Fails open** on a Redis outage (logged every
  time), matching the conversation-cache precedent (ADR 0008).
- **`guardrails.py` — two `Guardrail` implementations.**
  - `InjectionPatternGuardrail` screens **retrieved excerpts** for
    injection-shaped text — a **logged signal, never a drop** (ADR 0014's fence
    stays load-bearing; this is defense-in-depth telemetry).
  - `UserInputGuardrail` screens the **user's chat input**; blocks a small,
    high-confidence set of direct override/extraction attempts, logs the rest.

The **answer-egress check** (the second RAG-hardening addition) is a dedicated
function in [`services/retrieval/egress.py`](../retrieval/egress.py) — it lives
there because it references retrieval's own fence-marker/preamble format.

## Wiring

- Auth + rate limiting are FastAPI dependencies applied to
  **`POST /v1/chat/completions` only** (`services/api/security.py`). `/health`,
  `/ready`, `/version`, `/metrics` stay unauthenticated (ADR 0019).
- The user-input guardrail runs in the chat route before the agent loop; the
  excerpt screen runs inside `DocumentSearch`; the egress check runs on the final
  answer of both the streamed and non-streamed paths.

## Deliberately deferred (ADR 0019)

JWT / OAuth2 / external IdP · role- or scope-based authZ · per-source RAG trust
tiering (ADR 0014 ties it to the corpus admission model changing) · a runtime
cloud secrets-manager integration (secret management this stage is CI scanning
only — `gitleaks` + `pip-audit`).

> The global security rules in `docs/coding-standards.md` (no secrets in code,
> env-only credentials, treat tool/file content as untrusted) apply from Stage 1
> onward; this package is the *enforcement components*.
