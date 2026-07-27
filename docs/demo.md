# Scripted demo walkthrough

A repeatable end-to-end demonstration of the platform's externally-visible
contract, run against the **real running stack** — not a mock, not paraphrased
output.

**Where it lives:** [`scripts/demo.sh`](../scripts/demo.sh) — a single runnable
script, chosen over a prose command list so a reviewer can *reproduce* it, not just
read it. This page is its companion: the exact commands and the **real captured
output** from one run.

## Running it

Bring the cost-free `test`-profile stack up first (ADR 0020 Mode 1 — the scripted
model echoes, so there is no model bill), then run the script:

```bash
docker compose -f docker-compose.yml -f docker-compose.test.yml up -d --build
./scripts/demo.sh
```

> **Capture note (this machine).** The output below was captured from a container
> on the compose network (`http://api:8000`), not `http://localhost:8000`. On this
> project's Windows box, Docker Desktop's host port-forward intermittently returns
> empty replies (exit 52) while the container serves 200s internally — a documented
> environment quirk (see `CLAUDE.md`), not a code fault. `scripts/demo.sh` targets
> `localhost:8000` by default for a normal machine and honours a `DEMO_HOST`
> override (`DEMO_HOST=http://api:8000`) for the network-internal path used here.

All output is from the `test` profile, so the model is the deterministic
`ScriptedLLMClient` that echoes the last user turn (`"You said: …"`). That is what
keeps the demo free and reproducible; the wire contract is identical to a real
model call.

---

## 1. Authenticated chat — `200` + response shape

```bash
curl -X POST http://api:8000/v1/chat/completions \
  -H 'Authorization: Bearer test-key-not-a-real-secret' -H 'Content-Type: application/json' \
  -d '{"model":"claude-opus-4-8","messages":[{"role":"user","content":"In one line, what is a readiness probe?"}],"max_tokens":64}'
```

```json
{"id":"chatcmpl-dea7b69f2b9b43ad8295da7492341545","object":"chat.completion","created":1785142949,"model":"claude-opus-4-8","choices":[{"index":0,"message":{"role":"assistant","content":"You said: In one line, what is a readiness probe?"},"finish_reason":"stop"}],"usage":{"prompt_tokens":8,"completion_tokens":10,"total_tokens":18},"citations":[]}
```
`HTTP 200` — OpenAI-shaped `choices` / `usage`, plus the platform's top-level
`citations` field (ADR 0013), here `[]` because this turn was not grounded.

## 2. Streaming (SSE) — `data:` frames + `[DONE]`

```bash
curl -N -X POST http://api:8000/v1/chat/completions \
  -H 'Authorization: Bearer test-key-not-a-real-secret' -H 'Content-Type: application/json' \
  -d '{"model":"claude-opus-4-8","messages":[{"role":"user","content":"Say hello in three words."}],"max_tokens":64,"stream":true}'
```

```
data: {"id":"chatcmpl-a2c5c1262f974ee287a13aed2446b65d","object":"chat.completion.chunk","created":1785142954,"model":"claude-opus-4-8","choices":[{"index":0,"delta":{"role":"assistant","content":"You"}}]}

data: {"id":"chatcmpl-a2c5c1262f974ee287a13aed2446b65d","object":"chat.completion.chunk","created":1785142954,"model":"claude-opus-4-8","choices":[{"index":0,"delta":{"content":" said:"}}]}

data: {"id":"chatcmpl-a2c5c1262f974ee287a13aed2446b65d","object":"chat.completion.chunk","created":1785142954,"model":"claude-opus-4-8","choices":[{"index":0,"delta":{"content":" Say"}}]}

... (one frame per token: " hello", " in", " three", " words.") ...

data: {"id":"chatcmpl-a2c5c1262f974ee287a13aed2446b65d","object":"chat.completion.chunk","created":1785142954,"model":"claude-opus-4-8","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"citations":[]}

data: [DONE]
```
`text/event-stream`: a role-priming frame, one content delta per token, a final
frame carrying `finish_reason` + `citations`, then the `data: [DONE]` sentinel
(ADR 0004). Same graph as the non-streamed path — streaming is transport only.

## 3. `document_search` / citations — grounded turn

```bash
curl -X POST http://api:8000/v1/chat/completions \
  -H 'Authorization: Bearer test-key-not-a-real-secret' -H 'Content-Type: application/json' \
  -d '{"model":"claude-opus-4-8","messages":[{"role":"user","content":"According to the platform documentation, what does the circuit breaker return when it trips?"}],"max_tokens":64}'
```

```json
{"id":"chatcmpl-2398b737660048969633186c9ffbfc29","object":"chat.completion","created":1785142959,"model":"claude-opus-4-8","choices":[{"index":0,"message":{"role":"assistant","content":"You said: According to the platform documentation, what does the circuit breaker return when it trips?"},"finish_reason":"stop"}],"usage":{"prompt_tokens":14,"completion_tokens":16,"total_tokens":30},"citations":[]}
```
`HTTP 200`, `citations: []`. **This is the honest limit of the cost-free profile:**
the `citations` field is always present, but the scripted model never emits a
`tool_use` block, so no `document_search` runs and no source chunks are attached.
A **non-empty** `citations` list requires a real model *and* an ingested corpus —
both of which came together later in the session (the `.env` key was fixed and the
corpus ingested). The **real non-empty capture is in the dev-profile addendum** at
the end of this page. The grounding + citation mechanism is also proven hermetically
by the retrieval and citation unit tests (ADR 0013/0014).

## 4. Unauthenticated call — the uniform `401`

```bash
curl -X POST http://api:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"claude-opus-4-8","messages":[{"role":"user","content":"ping"}],"max_tokens":16}'
```

```json
{
    "error": {
        "type": "http_error",
        "message": "Missing or invalid credentials.",
        "request_id": "88376b57549a4932b8702d8cfda72782"
    }
}
```
```
HTTP 401
```
The uniform error envelope, pretty-printed by the script, followed by the status
line (the script captures body and status separately so the `HTTP 401` line is not
fed into the JSON parser). Missing / malformed / wrong keys all return this **same**
shape — only the server log distinguishes why, so a client cannot probe the store
(ADR 0019).

## 5. Rate limiting — a real `429`

70 requests fired on one principal (`loaduser-7`) inside the 60 s window; the
limiter is 60 requests / 60 s per principal:

```
status codes: 200 ×60, then 429 ×10
200s: 60
429s: 10
```
Exactly 60 `200`s then `429` for the rest — the per-principal Redis token bucket
(atomic Lua `INCR`+`EXPIRE`) cutting in at the threshold (ADR 0019).

## 6. Circuit breaker — hermetic fault-injection fallback

The breaker wraps the **real** `AnthropicClient` only; under the `test` profile the
scripted client never fails, so the breaker never opens here, and no cost-free live
fault could be injected this session (there is no Anthropic base-URL override to
point at a dead endpoint). As the Stage 9/10 prompts both allow, its contract is
demonstrated hermetically instead:

```bash
uv run pytest tests/unit/test_resilience.py tests/unit/test_llm.py -q
```
```
....................................                                     [100%]
36 passed
```
These cover the generic `CircuitBreaker` state machine (opens on the Nth
transport/5xx failure, **never on a `400`**, half-open recovery after cooldown) and
`CircuitBreakingLLMClient` rendering an open breaker as `503 provider_unavailable`
(ADR 0020), driven by the `FaultInjectingLLMClient` double — the real client is
never dialled, the same posture as ADR 0009.

---

## What the demo shows, in one line each

| # | Concern | Result |
|---|---------|--------|
| 1 | Authenticated chat | `200`, OpenAI-shaped `choices`/`usage` + `citations` |
| 2 | SSE streaming | `data:` frames per token + `[DONE]` (ADR 0004) |
| 3 | Grounding / citations | non-empty typed `citations` captured for real (addendum) — model grounded its answer in `incident-response.md`, ADR 0013 |
| 4 | AuthN | one uniform `401` for missing/malformed/wrong (ADR 0019) |
| 5 | Rate limiting | 60 `200` then `429`, per principal (ADR 0019) |
| 6 | Circuit breaker | `503 provider_unavailable` contract, proven hermetically (ADR 0020) |

---

## Addendum — real dev-profile captures (Anthropic key fixed, 2026-07-27)

The cost-free walkthrough above is the reproducible, no-bill artifact (scripted
echo model). After it was captured, the operator fixed the malformed `.env`
`ANTHROPIC_API_KEY`, so the model-dependent steps were **re-captured for real**
against a `dev`-profile stack (`ENVIRONMENT=dev`, real `claude-opus-4-8`; auth key
overridden to the fixed test principal so the same bearer works). These are **real
Anthropic calls**, not echoes — note the substantive answers and the real token
counts (the ~1.1k prompt tokens are the system prompt + tool specs).

**Real authenticated chat (`200`):**
```json
{"id":"chatcmpl-cd4e0c9603704155a610a96cc2ae3ac0","object":"chat.completion","model":"claude-opus-4-8","choices":[{"index":0,"message":{"role":"assistant","content":"A readiness probe in Kubernetes is a periodic health check that determines whether a pod's container is ready to accept traffic, and if it fails, the pod is temporarily removed from the Service's load-balancing endpoints (without being restarted) until it passes again."},"finish_reason":"stop"}],"usage":{"prompt_tokens":1104,"completion_tokens":85,"total_tokens":1189},"citations":[]}
```

**Real streaming (SSE, one frame shortened):**
```
data: {…"delta":{"role":"assistant","content":"Circ"}}
data: {…"delta":{"content":"uit breaker, retry with backoff, bulkhead"}}
data: {…"delta":{},"finish_reason":"stop"},…"citations":[]}
data: [DONE]
```

**Real grounding / citations (step 3) — non-empty, captured.** After the operator
authorised it, the corpus was ingested (`scripts/ingest.py` → 4 chunks embedded via
real Voyage `voyage-3.5-lite`, 1685 tokens, into the `documents` collection). A real
grounded call then made the model call `document_search` and answer from the fenced
excerpts, returning a **non-empty typed `citations` array** (ADR 0013):

```
POST /v1/chat/completions
{"messages":[{"role":"user","content":"According to the documentation, what are the
 key steps in the incident response process?"}],"max_tokens":512}
```
```
HTTP 200 · usage: prompt_tokens 5110 · completion_tokens 587 · total 5697

message: "According to the incident response documentation (`incident-response.md:0`),
 the key steps are: 1. Classify the severity … 2. Run the incident (one incident
 commander who does not debug; mitigate before diagnosing; record as you go) …
 3. Review afterwards (blameless, within five working days; every action gets an
 owner and a date). … only `incident-response.md` describes the incident response
 process itself."

citations: [
  {"id":"incident-response.md:0","source":"incident-response.md","score":0.650, "text":"# Incident response … Severity is set by customer impact …"},
  {"id":"observability.md:0",     "source":"observability.md",     "score":0.484, "text":"# Observability … The three signals …"},
  {"id":"deployments.md:0",       "source":"deployments.md",       "score":0.367, "text":"# Deployments and rollback … blue/green cutover …"},
  {"id":"code-review.md:0",       "source":"code-review.md",       "score":0.351, "text":"# Code review … What review is for …"}
]
```
Each citation is **typed provenance carried out of the tool** — `id`, `source`,
`score`, and the retrieved `text` — never parsed back out of the model's answer, so
a document cannot forge its own citation (ADR 0013). The model grounded its answer
in the top hit and even noted the lower-scored documents were off-topic. (Text
bodies abbreviated here for readability; the corpus is the committed, non-sensitive
`data/corpus/` ops docs.)
