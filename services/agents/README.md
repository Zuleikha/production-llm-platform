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

## Tools

| Tool | Does | Lives in |
|------|------|----------|
| `calculator` | Evaluates an arithmetic expression | `tools.py` |
| `text_stats` | Character / word / line counts | `tools.py` |
| `json_query` | Reads one value from a JSON document by dotted path | `tools.py` |
| `document_search` | Searches the RAG corpus and returns cited excerpts | `services/retrieval/tool.py` (Stage 4) |

The three tools in `tools.py` are **deterministic and offline** — pure functions
of their arguments — and `ToolRegistry.default()` returns exactly those, which is
what lets the agent loop be exercised end to end in a hermetic suite.

`document_search` is the first tool that is not a pure function. It lives in
`services/retrieval/`, not here, and is injected into the registry at wiring time
(`ToolRegistry.with_tools`, called from `services.api.app`). The dependency runs
retrieval → agents, which keeps this package the generic agent kernel: it knows
what a tool *is*, not what any particular tool talks to. `Tool.run` is async from
Stage 4 so retrieval can do I/O like any other tool rather than being a special
case in the loop.

Two rules for anyone adding a tool:

- **The model is not a trusted caller.** It chooses the tool and its arguments,
  influenced by whatever text reached its context. `calculator` parses to an AST
  and walks an allow-list rather than calling `eval`, and caps exponents so an
  allow-listed operator cannot hang the process.
- **Tool results are untrusted input, and from Stage 4 that is a live concern.**
  A tool result goes straight back into the model's context. Through Stage 3
  every tool returned values derived solely from its own arguments, which made
  feeding results back unexamined safe. **`document_search` returns document
  text** — potentially attacker-controlled — which is exactly the case that
  breaks that assumption. The mitigation (nonce-fenced excerpts labelled as
  untrusted data, provenance carried out-of-band as typed citations) and, just
  as importantly, its documented limits (delimiting is not immunity; a persuasive
  injected instruction may still be obeyed) are in **ADR 0014** and in the tool
  module's own docstring. **Anyone adding a tool that reads outside data, or
  widening what can enter the corpus, is changing the threat model and must read
  ADR 0014 first.**

See ADR 0006 (loop) and ADR 0014 (retrieval-tool injection boundary).
