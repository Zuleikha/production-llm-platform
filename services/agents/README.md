# agents

> **Status: implemented in Stage 3 (agents).**

A single autonomous agent, and the tools it may call.

## Contract

`Agent` (see `base.py`) is the abstract base for a single agent.

| Method | Signature | Meaning |
|--------|-----------|---------|
| `run` | `async run(task: str) -> str` | Execute a task and return the final answer. |

`ToolAgent` is the concrete implementation: a thin adapter over the LangGraph
loop in `services/orchestrator/graph.py`. The loop, step cap and tool dispatch
live in the graph because the orchestrator needs the same mechanics for
streaming and would otherwise reimplement them.

`Agent` is deliberately narrow — task in, answer out. Conversations, persistence
and transports belong to the orchestrator above it, which is what lets the same
agent be driven by a chat request today and an evaluation harness in Stage 6.

## Tools (`tools.py`)

| Tool | Does |
|------|------|
| `calculator` | Evaluates an arithmetic expression |
| `text_stats` | Character / word / line counts |
| `json_query` | Reads one value from a JSON document by dotted path |

Every tool is **deterministic and offline** — a pure function of its arguments.
That is what lets the agent loop be exercised end to end in a hermetic test
suite. Retrieval / vector-search tools belong to **Stage 4** and must not be
added here.

Two rules for anyone adding a tool:

- **The model is not a trusted caller.** It chooses the tool and its arguments,
  influenced by whatever text reached its context. `calculator` parses to an AST
  and walks an allow-list rather than calling `eval`, and caps exponents so an
  allow-listed operator cannot hang the process.
- **Tool results are untrusted input.** They go straight back into the model's
  context. Today's tools only return values derived from their own arguments,
  which is what makes that safe — Stage 4 changes that and must revisit it.

See ADR 0006.
