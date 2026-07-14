# Architecture Decision Records

One ADR per significant decision — never a single combined file. ADRs are
immutable once accepted: to change a decision, add a new ADR that supersedes the
old one and update the old one's status.

## Format

Every ADR uses: **Context** (the forces at play) → **Decision** (what we chose)
→ **Consequences** (positive *and* negative), plus **Status** and **Date**.

## Index

| ADR | Title | Status | Stage |
|-----|-------|--------|-------|
| [0001](0001-stack-selection.md) | Stack selection | Accepted | 1 |
| [0002](0002-repo-structure.md) | Repository structure | Accepted | 1 |
| [0003](0003-configuration-approach.md) | Configuration approach | Accepted | 1 |
| [0004](0004-streaming-transport.md) | Streaming transport for chat completions | Accepted | 2 |
| [0005](0005-datastore-connection-pooling.md) | Datastore connection pooling and readiness | Accepted | 2 |

## Conventions

- Filename: `NNNN-kebab-case-title.md`, zero-padded, sequential.
- Status: `Proposed` → `Accepted` → (`Superseded by NNNN` | `Deprecated`).
- Record the trade-off you accepted, not just the option you picked.
