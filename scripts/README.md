# scripts

Developer helper scripts. Nothing here is required by the application at
runtime — CI calls the same underlying commands directly.

| Script | Platform | Purpose |
|--------|----------|---------|
| `verify.ps1` | Windows / PowerShell | Full local quality gate: sync, ruff, format, mypy, pytest. |
| `verify.sh` | bash | Same gate for Linux / Git Bash / CI parity. |
| `smoke_health.sh` | bash | Poll `/health` until 200 after `docker compose up`, then check `/ready`, `/version`, `/metrics`. |

> `UV_LINK_MODE=copy` is exported by both verify scripts — the uv cache lives on
> `C:` while this repo is on `D:`, and cross-drive hardlinking is unreliable.
> See "Known environment quirks" in `CLAUDE.md`.
