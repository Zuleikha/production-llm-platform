# orchestrator — interface stub

> **Status: planned, not yet implemented.** Delivered from **Stage 3 onward**
> (agent & workflow orchestration). Stage 1 ships only the contract below.

## Intended contract

`Orchestrator` (see `base.py`) coordinates a multi-step workflow — a graph of
agents, tool calls, and retrieval steps — and returns the final state.

| Method | Signature | Meaning |
|--------|-----------|---------|
| `run` | `async run(request: Mapping[str, object]) -> Mapping[str, object]` | Run the workflow, return result state. |

## Planned scope

- **Stage 3:** LangGraph-based agent orchestration (state machines, routing).
- **Stage 6:** MLOps pipelines (batch/eval orchestration).

Every method currently raises `NotImplementedError`.
