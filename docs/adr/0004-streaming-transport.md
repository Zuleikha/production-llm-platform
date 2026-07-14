# ADR 0004 — Streaming transport for chat completions

- **Status:** Accepted
- **Date:** 2026-07-14
- **Stage:** 2 (API)

## Context

The chat completion endpoint must stream. Token-by-token delivery is not a
nicety for an LLM product: a 20-second wait for a complete answer reads as
broken, while first-token-in-200ms reads as fast even when total latency is
identical.

Stage 2 has no model — the engine is a deterministic mock (`EchoEngine`). The
point of building streaming now is to prove the transport, the framing and the
client contract *before* Stage 3 introduces a real model, so that when streaming
misbehaves later we already know the plumbing is sound.

Forces:

- Generation is **one-directional and short-lived**: client asks once, server
  emits until done. There is no client-to-server traffic mid-generation.
- Stage 3's backends (Anthropic, OpenAI) already stream over SSE. A transport
  that mirrors their wire format means the seam is a pass-through, not a
  translation layer.
- Stage 7 puts this behind Kubernetes ingress; Stage 8 adds auth. Both are
  markedly simpler for plain HTTP than for a socket upgrade.

## Decision

**Server-Sent Events over HTTP POST**, framed to mirror the OpenAI wire format.

The response carries `Content-Type: text/event-stream`, plus `Cache-Control:
no-cache` and `X-Accel-Buffering: no` so intermediaries do not buffer a live
stream into a single lump — which silently defeats the entire feature.

Frames are one JSON object per `data:` line, terminated by a literal sentinel:

```
data: {"id":"chatcmpl-…","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant","content":"You"}}]}

data: {"id":"chatcmpl-…","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" said:"}}]}

data: {"id":"chatcmpl-…","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]

```

Conventions worth stating, because clients depend on them:

- **One completion `id` spans the whole stream.**
- **The first chunk carries `role`**, subsequent chunks carry `content` only.
- **The final chunk carries `finish_reason` and an empty `delta`.**
- Unset fields are **omitted, not null** (`model_dump_json(exclude_none=True)`).
- `data: [DONE]` terminates. It is redundant with EOF, but it lets a client
  distinguish *"the server finished"* from *"the connection dropped"* — which
  EOF alone cannot.

Streaming is opt-in per request via `"stream": true`. Both paths call the same
`CompletionEngine`, so **the transport cannot change the answer** — a test
asserts the concatenated stream equals the non-streamed body.

### Rejected alternatives

| Alternative | Why not |
|-------------|---------|
| **WebSockets** | Bidirectional, stateful, and pays for capability we do not use — generation never needs client→server traffic mid-flight. Costs a socket upgrade through ingress, bespoke auth (headers are awkward on the handshake), no free reconnect, and its own heartbeat/liveness design. Right answer for a duplex agent UI; wrong for one-way token delivery. |
| **Chunked JSON / NDJSON** | Works, and is barely simpler than SSE over the wire. But it is a bespoke format: no `EventSource`, no framing standard, no reconnect semantics, and every client writes its own line-splitter. SSE gets the same bytes with a spec attached. |
| **Long polling** | Reintroduces exactly the latency streaming exists to remove. |
| **gRPC server streaming** | Genuinely good at this, but forces a second protocol, a proto toolchain and a browser proxy (grpc-web) onto a stack that is otherwise plain HTTP+JSON. Not worth it for one endpoint. |
| **A bespoke frame format** | Mirroring OpenAI's shape means existing SDKs and client code work unchanged, and Stage 3's backends need no translation. Novelty here buys nothing. |

## Consequences

**Positive**

- Plain HTTP: works through ingress, proxies, and Stage 8's header-based auth
  with no special handling.
- Mirrors the format Stage 3's model backends already emit, so the engine seam
  stays a pass-through.
- Browser clients can use built-in `EventSource`; everyone else reads lines.
- The `[DONE]` sentinel makes truncation detectable client-side.

**Negative / accepted trade-offs**

- **SSE is one-directional.** Cancelling mid-generation needs a separate call,
  not an upstream message. Acceptable now; revisit if Stage 3 wants interactive
  interrupts (that is the trigger to reconsider WebSockets).
- **`EventSource` cannot POST.** Browsers must use `fetch` + a stream reader, as
  the prompt lives in the request body. Same constraint every LLM API has.
- **Buffering proxies can defeat it.** Mitigated by the headers above, but a
  misconfigured intermediary can still ruin the experience invisibly — the
  failure mode is a slow lump, not an error.
- **No automatic reconnect.** We do not send `id:`/`retry:` fields, so
  `EventSource`'s built-in resume is unused. Resuming a half-finished completion
  needs server-side state we do not have; a dropped stream is retried whole.
- Streaming responses bypass the `response_model`, so the streamed shape is not
  enforced by FastAPI — the tests carry that weight instead.
