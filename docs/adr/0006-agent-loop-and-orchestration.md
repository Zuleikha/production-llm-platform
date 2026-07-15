# ADR 0006 — Agent loop, orchestration and the model client

- **Status:** Accepted
- **Date:** 2026-07-15
- **Stage:** 3 (Agents)

## Context

Stage 2 built the endpoint, the schemas and the SSE plumbing against a mock:
`EchoEngine` behind a `CompletionEngine` protocol. It called no model, and its
`usage` numbers were the route splitting the reply on whitespace. Stage 3 has to
put a real agent behind that seam without redesigning the endpoint.

Four forces shape this:

- **The seam has to hold.** The whole point of Stage 2's `CompletionEngine` was
  that Stage 3 could swap the engine without touching a route. If we redesign the
  wire format now, the seam was theatre.
- **An agent is several model calls, not one.** Reasoning, tool calls, and a
  final answer are separate round trips. Anything that assumes one request = one
  model call (token accounting, most obviously) is wrong before it is written.
- **Streaming and non-streaming must not diverge.** Two code paths that produce
  "the answer" is how you get a bug that only reproduces over SSE.
- **The model is not a trusted caller.** It chooses which tools run and with what
  arguments, and its choices are influenced by whatever text reached its context.

## Decision

### LangGraph for the loop, as an explicit state machine

The loop is a compiled `StateGraph` (`services/orchestrator/graph.py`):

```
START -> reason -> (tool_calls and steps < max? -> act -> reason) -> END
```

`reason` calls the model. `act` executes every requested tool and appends the
results. The conditional edge routes back so the model can observe them.

A `while` loop would work today. The graph is worth its weight because the
routing is *data* — the step cap, the branch condition and the state channels are
inspectable and testable in isolation, and Stage 6's evaluation harness needs to
drive the same machine without the HTTP layer. Token usage accumulates through a
reducer (`Annotated[TokenUsage, _accumulate_usage]`) rather than by mutation,
which is what makes "sum every model call" a property of the graph rather than a
thing each call site remembers.

### The Anthropic SDK directly — `langchain` is dropped, not promoted

The `orchestration` extra pinned `langchain==1.3.13` and `langgraph==1.2.9`.
**Only `langgraph` is promoted**; `anthropic` is added; `langchain` is removed
from the project entirely.

The graph calls `anthropic.AsyncAnthropic` directly rather than going through
`langchain-anthropic`'s `ChatAnthropic`. The reason is this stage's actual
requirements:

- **Token accounting.** The stage requires the provider's real
  `usage.input_tokens` / `output_tokens`. The SDK hands those over verbatim. A
  LangChain chat model normalises usage into its own shape, which is one more
  translation between us and the number on the invoice.
- **Streaming.** We need the raw `content_block_delta` events to map onto ADR
  0004's SSE frames. The SDK gives them directly.
- **Replaying tool blocks.** `tool_result` must reference a `tool_use` block
  exactly as the model emitted it. Fewer layers between us and that JSON is
  strictly better.

`langgraph` does pull `langchain-core` in transitively. That is fine — it is a
transitive dependency of a library we chose, not an abstraction we code against.

### Usage is reported by the engine, not computed by the route

`Completion` (`services/api/completions.py`) carries `text`, `usage` and
`finish_reason`. This **is** a change to the route, and it is deliberate: the
route cannot know what a turn cost, because a run spans several model calls it
never sees. Stage 2's whitespace count was the fake this stage exists to replace,
and the honest fix is to move the number to the only layer that has it.

The endpoint contract, the schemas, the SSE frame format and the `[DONE]`
sentinel are all unchanged.

### `temperature` is accepted and not forwarded

`ChatCompletionRequest.temperature` stays in the schema for wire compatibility,
and stops at the HTTP boundary. **Claude Opus 4.7+ removed `temperature`,
`top_p` and `top_k` and rejects them with a 400.** Forwarding it would break
every request; removing the field would break clients that send it. Accepting and
ignoring it is the least-bad option, and it is documented in the field
description and in `AnthropicClient.stream`.

This is a genuine wart. Stage 2 never forwarded it either (the route dropped it),
so nothing regressed — but a client that sets `temperature` expecting an effect
will not get one.

### `prod` refuses to boot without `ANTHROPIC_API_KEY`

The same validator and the same reasoning as ADR 0005's datastore URLs: a
production service whose every chat request 401s should never have started. The
error names the variable. Dev and test are exempt.

### Tools are deterministic, offline and domain-agnostic

Three tools: `calculator`, `text_stats`, `json_query`. Every one is a pure
function of its arguments. That is what lets the agent loop be exercised end to
end in a hermetic suite — a tool calling a live API would make the tests flaky
and the loop untestable. Retrieval tools belong to Stage 4.

`calculator` parses to an AST and walks an allow-list. **It must never become
`eval`**: the model chooses the expression, and the model's choice is influenced
by text we did not write. It also caps exponents, because `2 ** 10**10` is an
allow-listed way to hang the process.

### Failures inside the loop are fed back, not raised

A tool that fails returns `is_error: true` to the model. The model asked for
something that did not work; the useful response is to say so and let it correct
itself. Raising throws away a run the caller already paid for. An *unexpected*
tool exception is logged with its trace (it is our bug) but still returns an
error result, for the same reason.

## Rejected alternatives

| Alternative | Why not |
|-------------|---------|
| A plain `while` loop | Works, but the routing and step cap become implicit and the loop is harder to drive from Stage 6's harness without the HTTP layer. The graph makes the state machine data. |
| `langchain-anthropic` / `ChatAnthropic` | Normalises usage and streaming into LangChain's shapes — the two things this stage most needs verbatim from the provider. Adds a large tree to own for no capability we use. |
| Promote `langchain` alongside `langgraph` | Nothing imports it. Pinning a dependency the code does not use is how a lockfile becomes fiction. |
| `langgraph`'s prebuilt ReAct agent | Hides the loop this stage exists to build, and its usage/streaming surface is the same abstraction problem one level up. |
| Keep computing usage in the route | The route cannot see the calls it must count. This is the fake being replaced. |
| Fake the whole engine in tests | Would leave the graph, tool dispatch and SSE mapping untested. Fake the *client* instead — see ADR 0009. |
| Keep `EchoEngine` as the test double | It implements the protocol, not the agent — testing against it proves nothing about the loop. `ScriptedLLMClient` sits one layer lower and leaves the real machinery in the test. |
| Drop `temperature` from the schema | Breaks clients sending an OpenAI-shaped body for a parameter that was already inert. |
| Forward `temperature` to Anthropic | 400s every request on Opus 4.7+. |
| No step cap | A model that keeps requesting tools is an unbounded bill. |
| Raise on tool failure | Discards a recoverable run and the tokens spent on it. |

## Consequences

**Positive**

- The Stage 2 seam is proven, not asserted: the engine was replaced wholesale and
  the endpoint did not change.
- `usage` matches the provider's numbers, summed across the whole run.
- One graph serves both transports, so SSE cannot drift from JSON.
- The loop, the tools and the step cap are all unit-testable without a network.
- Base dependencies gained two libraries and lost one.

**Negative / accepted trade-offs**

- **`temperature` is silently inert.** Documented in the schema and here; there is
  no good alternative while the model family rejects it.
- **No prompt caching.** The system prompt and tool specs are re-sent every call.
  Tool specs are already sorted for a stable prefix, so this is a later win, not a
  rewrite.
- **No context compaction.** A long enough conversation will eventually exceed the
  context window and fail. Acceptable while conversations are short; Stage 9's
  problem, or an earlier one if it bites.
- **No retry or circuit breaking** beyond the SDK's own defaults (2 retries on
  429/5xx). Stage 9 owns this.
- **The step cap ends a run mid-thought.** Hitting it returns the model's last
  text, which may be a preamble to a tool call that never happened. Logged as a
  warning; rare in practice with a cap of 6.
- **`ScriptedLLMClient` lives in application code**, not in tests, because the
  factory must be able to return it. It is real, working code and the price of
  making the test profile hermetic by construction (ADR 0009).
