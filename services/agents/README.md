# agents — interface stub

> **Status: planned, not yet implemented.** Delivered in **Stage 3 (agents)**.
> Stage 1 ships only the contract below — no behaviour.

## Intended contract

`Agent` (see `base.py`) is the abstract base for a single autonomous agent.

| Method | Signature | Meaning |
|--------|-----------|---------|
| `run` | `async run(task: str) -> str` | Execute a task and return the final answer. |

## Planned scope (Stage 3)

- Tool-use / function-calling agents built on **LangChain** + **LangGraph**.
- Deterministic agent loop with step limits and structured tool results.
- Treats tool output as untrusted input (prompt-injection aware).

Every method currently raises `NotImplementedError`. Do not add working logic
here before Stage 3.
