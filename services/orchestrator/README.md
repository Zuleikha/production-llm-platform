# orchestrator

> **Status: implemented in Stage 3 (agents).** Stage 6 extends it with MLOps /
> evaluation pipelines.

Coordinates a multi-step workflow — the agent loop, its tools, the model client
and conversation state — and returns the final state.

## Contract

`Orchestrator` (see `base.py`) is the abstract base.

| Method | Signature | Meaning |
|--------|-----------|---------|
| `run` | `async run(request: Mapping[str, object]) -> Mapping[str, object]` | Run the workflow, return result state. |

`AgentOrchestrator` is the concrete implementation. `run` is the generic,
mapping-shaped door the ABC requires; `answer` / `answer_stream` are the typed
paths the API actually uses.

## Modules

| Module | Responsibility |
|--------|----------------|
| `base.py` | `Orchestrator` ABC + `AgentOrchestrator` — history, usage, persistence |
| `graph.py` | `AgentGraph` — the LangGraph state machine: `reason ⇄ act` |
| `llm.py` | `LLMClient` protocol, `AnthropicClient`, `ScriptedLLMClient`, `build_llm_client` |
| `conversations.py` | `ConversationStore` protocol, Postgres store, Redis read-through cache |

## Things that are load-bearing

- **One graph serves both transports.** `reason` pushes text into LangGraph's
  custom stream; a streaming caller reads it, a non-streaming caller invokes the
  same graph and reads final state. There is no second loop for SSE to drift
  from. See ADR 0006.
- **The `test` profile cannot construct `AnthropicClient`.** It raises before the
  key is read. Use `build_llm_client(settings)`, which returns the scripted
  double under `test`. This is a mechanism, not a convention — see ADR 0009.
- **Postgres owns conversation history; Redis only caches it.** Writes invalidate
  rather than update, Postgres first. See ADR 0008.
- **No `temperature`.** Claude Opus 4.7+ rejects sampling parameters with a 400.
  The API schema still accepts the field; it stops at the HTTP boundary.

See ADR 0006, 0008, 0009.
