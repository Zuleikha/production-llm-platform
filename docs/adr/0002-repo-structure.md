# ADR 0002 — Repository structure

- **Status:** Accepted
- **Date:** 2026-07-14
- **Stage:** 1 (foundation)

## Context

The platform will grow to include an API, agents, orchestration, RAG,
evaluation, monitoring and security over ten stages. If the layout is invented
incrementally, each stage bolts its code wherever is convenient and the result is
a ball of mud. We need a layout that already has a labelled home for every future
component, without pretending those components exist.

## Decision

### Layout

```
docs/            architecture, ADRs, runbooks, stage summaries, prompts
services/        one subpackage per logical service
  api/           ← the ONLY implemented service in Stage 1
  orchestrator/  agents/  retrieval/  evaluation/  monitoring/  security/   ← stubs
shared/          cross-cutting foundation (config, logging, observability, version)
config/          committed non-secret env profiles
tests/           mirrors the source tree (tests/unit/...)
examples/        runnable usage examples
infrastructure/  docker/  kubernetes/  terraform/
.github/workflows/  CI
scripts/         developer helper scripts
```

### Stub policy (the important part)

A folder whose real content belongs to a later stage contains **only**:

1. a `README.md` stating the intended contract, the owning stage, and an explicit
   "planned, not yet implemented" status, and
2. where useful, an abstract base class or `Protocol` with typed signatures,
   docstrings, and bodies that `raise NotImplementedError`.

A stub that raises is correct. **A working mini-version of a future component is
not** — it invites drift, gets depended on, and has to be deleted later.

Stage ownership: `api` → 2 · `agents` → 3 · `retrieval` → 4 · `monitoring` → 5 ·
`evaluation` → 6 · `orchestrator` → 3+ · `security` → 8 ·
`infrastructure/kubernetes|terraform` → 7.

### `shared/` vs `services/`

`shared/` holds cross-cutting concerns imported by every service (config,
logging, tracing, version). Business logic never lives there. This prevents the
common failure where `services/api` becomes a de-facto library that other
services import.

### Application, not library

`[tool.uv] package = false`: this repo is deployed, not published to an index. No
build backend, no `src/` layout, no install step. Imports resolve from the repo
root (`pythonpath = ["."]` for pytest, `PYTHONPATH=/app` in the image).

## Consequences

**Positive**
- Every future stage has an obvious, pre-labelled destination.
- Contracts are visible and reviewable before implementations exist.
- `NotImplementedError` makes accidental use of an unbuilt component fail loudly and immediately.
- Readers can tell what is real from what is planned without reading code.

**Negative / accepted trade-offs**
- Many near-empty directories in Stage 1 — accepted as the cost of a stable map.
- Stub contracts will need revision as reality lands; ADRs record the changes.
- A flat (non-`src/`) layout risks accidental shadowing of installed packages; accepted because the package names (`shared`, `services`) are project-specific.
- Splitting one deployable into seven service packages is speculative up front; accepted because the stage plan already commits to those boundaries.
