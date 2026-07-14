# ADR 0003 — Configuration approach

- **Status:** Accepted
- **Date:** 2026-07-14
- **Stage:** 1 (foundation)

## Context

The platform must run as `dev`, `test` and `prod` with different logging,
debugging and (later) credentials. Two failure modes are common and both are
expensive: secrets committed to the repo, and configuration that fails at 3am in
production because a typo'd env var was read as a string and defaulted silently.

## Decision

### One typed settings object

A single `pydantic-settings` `Settings` class (`shared/config.py`) is the only
way config enters the process. Fields are typed and validated (`api_port` is
constrained to `1..65535`; `environment` and `log_format` are `Literal`s). Invalid
config fails at startup — loudly — rather than at first use.

Access is via `get_settings()`, `lru_cache`d so the whole process shares one
validated instance. FastAPI consumes it with `Depends(get_settings)`, which keeps
handlers testable (tests override settings without patching globals).

### Layered precedence

Highest wins:

1. **OS environment variables** — what Docker Compose, Kubernetes and CI inject.
2. **`.env`** at the repo root — a developer's local overrides. Git-ignored.
3. **`config/environments/<ENVIRONMENT>.env`** — committed, non-secret defaults.
4. **Field defaults** in `Settings`.

`ENVIRONMENT` (default `dev`) selects which profile file is loaded. Because OS
env sits above the profile file, a container can override any single value
without a new image — demonstrated in `docker-compose.yml`, which sets
`ENVIRONMENT=dev` but overrides `LOG_FORMAT=json`.

### Secrets

**Committed files contain no secrets — ever.** The profile files hold only
non-sensitive defaults (log level, port, service name). Credential fields
(`database_url`, `redis_url`, `qdrant_url`) default to `None` and are populated
exclusively from the OS environment at deploy time. `.env` is git-ignored;
`.env.example` documents the shape with placeholders only. A unit test asserts
the credential fields default to `None`, so a hardcoded secret fails CI.

Those three fields are declared but **unused in Stage 1** — the app connects to
nothing. They exist to fix the pattern before there is pressure to shortcut it.

### Rejected alternatives

| Alternative | Why not |
|-------------|---------|
| Raw `os.environ` reads at call sites | Untyped, unvalidated, untestable, scattered. |
| A committed `config.yaml` per environment | Invites secrets into git; duplicates what env vars already do well in containers. |
| Separate `Settings` subclass per environment | Class explosion; profile differences are data, not behaviour. |
| A secrets manager (Vault/KMS) now | Real answer for prod, but belongs with the security stage — env injection is the correct seam and does not block it. |

## Consequences

**Positive**
- Config is typed, validated, and discoverable in one file.
- The same image runs in every environment; only injected env differs.
- Committing a secret requires actively defeating a test and `.gitignore`.
- Tests switch profiles with `monkeypatch.setenv` + `get_settings.cache_clear()`.

**Negative / accepted trade-offs**
- `lru_cache` means tests must call `cache_clear()` when changing env — a known footgun, mitigated by doing it inside the `settings` fixture.
- Env vars are flat strings; deeply nested config would need a different mechanism (not needed yet).
- Committed profile files can drift from `.env.example`; no test enforces that yet (see technical debt in the stage summary).
- Stage 1 carries three unused credential fields — a deliberate, documented placeholder.
