# Development guide

## Prerequisites

| Tool | Version | Verify | Install (Windows) |
|------|---------|--------|-------------------|
| Python | 3.12 | `python --version` | `winget install Python.Python.3.12` |
| uv | ≥ 0.11 | `uv --version` | `winget install astral-sh.uv` |
| Docker Desktop | Compose v2, WSL2 | `docker compose version` | `winget install Docker.DockerDesktop` (needs GUI + reboot) |
| git | any | `git --version` | `winget install Git.Git` |

> ⚠️ **Read [Known environment quirks](../CLAUDE.md#known-environment-quirks) in
> `CLAUDE.md` before your first `uv sync`** — this repo runs cross-drive and
> requires `UV_LINK_MODE=copy`, and a stripped/embeddable Python will produce a
> venv that segfaults on `import ctypes`.

## First-time setup

```bash
git clone <repo> && cd production-llm-platform

# Use a uv-managed interpreter (avoids broken system Pythons).
uv python install 3.12
uv venv --managed-python --python 3.12

uv sync                       # base + dev dependencies
uv run pre-commit install     # enable hooks on every commit
```

Optional local overrides: `cp .env.example .env` (git-ignored).

## Running the API

```bash
# Hot reload, console logs (dev profile is the default)
uv run uvicorn services.api.app:app --reload

# Or via the module entrypoint (uses API_HOST/API_PORT from settings)
uv run python -m services.api
```

Then: http://localhost:8000/health · http://localhost:8000/docs

## Running the full stack

```bash
docker compose up -d --build
./scripts/smoke_health.sh      # waits for /health = 200, then checks the rest
docker compose logs -f api     # structured JSON logs
docker compose down            # add -v to wipe data volumes
```

See the [local stack runbook](runbooks/local-stack.md) for ports and troubleshooting.

## The quality gate

Run everything CI runs, in one command:

```bash
pwsh scripts/verify.ps1        # Windows
./scripts/verify.sh            # bash
```

Individually:

```bash
uv run ruff check . --fix      # lint (autofix)
uv run ruff format .           # format
uv run mypy                    # strict type check
uv run pytest -v               # tests
```

## Configuration profiles

`ENVIRONMENT` selects the profile (`dev` default, `test`, `prod`), loading
`config/environments/<ENVIRONMENT>.env`. OS env vars override the file; `.env`
sits between. Full precedence: **ADR 0003**.

```bash
ENVIRONMENT=prod uv run python -m services.api    # JSON logs, debug off
LOG_LEVEL=DEBUG  uv run python -m services.api    # override one value
```

`LOG_LEVEL=DEBUG` also surfaces `@traced` span enter/exit/duration events.

## Dependencies

```bash
uv add <pkg>==<version>        # exact pin — never a range (ADR 0001)
uv sync --extra orchestration  # future-stage extras: orchestration|retrieval|observability|datastores
uv lock                        # after editing pyproject.toml — commit uv.lock
```

## Adding an endpoint

1. Create/extend a router in `services/api/routes/`.
2. Pydantic models for request **and** response; annotate everything.
3. `@traced` on the handler; `SettingsDep` for config.
4. Include the router in `create_app()`.
5. Write the test first, in `tests/unit/`.
6. Run the quality gate.

## Making a significant decision

Add an ADR: copy the format of `docs/adr/0003-*`, take the next number, and
record **Context / Decision / Consequences / Status** — including the downsides
you accepted. Index it in `docs/adr/README.md`.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `uv sync` installs nothing / import crashes | `UV_LINK_MODE=copy`; verify `uv pip list` against `uv.lock` |
| `import ctypes` access violation | System Python is embeddable — recreate the venv with `uv venv --managed-python` |
| mypy fails on a library's callback signature | Narrow with `isinstance`; don't add a blanket ignore |
| Test fails after changing profiles | Call `get_settings.cache_clear()` |
| Docker daemon unreachable | Start Docker Desktop and wait for the whale icon |
