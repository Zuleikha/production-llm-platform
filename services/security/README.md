# security — interface stub

> **Status: planned, not yet implemented.** Delivered in **Stage 8 (security
> hardening)**. Stage 1 ships only the contracts below.

## Intended contract

| Type | Kind | Meaning |
|------|------|---------|
| `AuthProvider` | ABC | `async authenticate(credential) -> principal_id`. |
| `Guardrail` | ABC | `async check(text) -> bool` (allow / block). |

## Planned scope (Stage 8)

- Authentication & authorization for API endpoints.
- Input/output guardrails (prompt-injection, PII, jailbreak screening).
- Rate limiting, secret management, dependency/security scanning in CI.

> The global security rules in `docs/coding-standards.md` (no secrets in code,
> env-only credentials, treat tool/file content as untrusted) apply from Stage 1
> onward — this stub is about *enforcement components*, not those rules.

All abstract methods currently raise `NotImplementedError`.
