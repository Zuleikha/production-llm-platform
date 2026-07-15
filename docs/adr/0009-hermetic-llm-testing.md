# ADR 0009 — The test profile cannot call Anthropic

- **Status:** Accepted
- **Date:** 2026-07-15
- **Stage:** 3 (Agents)

## Context

From Stage 3 the chat endpoint calls a paid API. The test suite drives that
endpoint. Those two facts have to be reconciled before the suite is trustworthy.

"We mock it in tests" is the usual answer and it is not good enough here, for
reasons that are specific rather than pedantic:

- **A developer's machine is not CI.** `ANTHROPIC_API_KEY` is normally exported
  on a machine where this project is being worked on. If the guard is "no key, no
  call", then the suite behaves differently — and bills — for the person writing
  the code, and passes silently in CI. That is the worst possible split.
- **A mock is opt-in, so it can be opted out of by accident.** One test that
  builds the app a slightly different way, one fixture that runs in the wrong
  order, one `create_app()` in a new test file — and something dials out. The
  failure is a charge on a bill, not a red test.
- **Stage 2 already solved the shape of this problem.** The datastores are
  hermetic *by construction*: the test profile sets no URLs, so an unconfigured
  store is never dialled. Nothing has to remember to patch anything. The LLM
  should work the same way.

## Decision

**Under the `test` profile, a real Anthropic client cannot be constructed.** Not
"is not" — *cannot*. Two independent mechanisms, both keyed on the profile and
neither on the key's presence:

### 1. The factory selects the double

```python
def build_llm_client(settings: LLMClient):
    if settings.is_test:
        return ScriptedLLMClient()
    return AnthropicClient(settings)
```

`create_app` goes through this. Under `test`, `AnthropicClient.__init__` is never
reached.

### 2. The real client refuses to construct

```python
class AnthropicClient:
    def __init__(self, settings):
        if settings.is_test:
            raise RuntimeError("AnthropicClient must never be constructed under the 'test' profile...")
```

This is the load-bearing one. It fires **before the key is read**, so it holds for
any code that reaches past the factory — a future test, a future stage, someone
constructing the client directly. There is no import order, fixture order, or
monkeypatch that turns a unit test into a paid call.

The guard keys on `settings.is_test`, **not** on `anthropic_api_key is None`.
That is the entire point: a developer with a real key exported gets exactly the
same hermetic suite CI gets.

### The double is real code, not a stub

`ScriptedLLMClient` implements `LLMClient` honestly: it streams its text in
pieces like the real client, reports usage, records what it was asked, and can be
scripted with a sequence of turns (including `tool_use` turns and
`stop_reason: "max_tokens"`). Running out of script raises loudly rather than
inventing a turn — a silent extra turn would make a scripted test assert nothing.

This is what lets the tests substitute **only the network**. The graph, the tool
registry, the orchestrator, the conversation store, the route and the SSE framing
are all the real thing in every test. Contrast with stubbing the whole engine,
which would leave every one of those untested.

### Where the double lives

In `services/orchestrator/llm.py` — application code, not `tests/`. The factory
has to be able to return it, and application code cannot import from the test
tree. This is the price of enforcement-by-construction and it is worth paying;
the class is documented as what it is.

### What is tested

`tests/unit/test_llm.py::TestTestProfileCannotCallAnthropic` pins the
*mechanism*:

- the real client raises under `test` even with a valid-looking key passed in;
- the factory returns the double under `test`;
- **a real key in the actual OS environment does not change either** — the case
  that distinguishes hermetic from merely-unconfigured;
- the running suite is, in fact, on the double.

If someone later replaces the guard with a convention, these fail.

## Rejected alternatives

| Alternative | Why not |
|-------------|---------|
| **`monkeypatch` / `unittest.mock` per test** | Opt-in. One forgotten patch is a real API call, and the failure is a charge rather than a red test. |
| **An autouse fixture that patches the client** | Better, but still a convention: it is scoped to the suite that defines it, and code reaching the client another way escapes it. |
| **Guard on "no API key set"** | The developer/CI split described above — the suite behaves differently, and bills, exactly where the code is being written. |
| **Record/replay (VCR-style cassettes)** | Requires real calls to record, and cassettes rot silently against a changing API — passing tests that describe last year's API. Worth revisiting for a contract test against the real API, which is a different job from this. |
| **A fake HTTP server on `base_url`** | Tests the SDK's HTTP layer, which Anthropic already tests. The seam that matters is our `LLMClient`, not their transport. |
| **Stub the whole `CompletionEngine`** | Leaves the graph, tools, orchestrator, persistence and SSE mapping untested — i.e. everything Stage 3 built. |
| **Let the suite call the real API** | Slow, flaky, non-deterministic, and it costs money on every CI run. |

## Consequences

**Positive**

- **CI needs no `ANTHROPIC_API_KEY`** and cannot spend money. The suite is
  identical on a laptop with a key exported and on a runner without one.
- Only the network is substituted — everything Stage 3 built is exercised for
  real in tests.
- Tool-use loops, `max_tokens` stops and multi-turn usage accumulation are all
  scriptable deterministically.
- The enforcement is tested, so it cannot quietly rot into a convention.

**Negative / accepted trade-offs**

- **Nothing here proves our assumptions about the real API are correct.** The
  double encodes what we believe about field names, streaming events and
  `stop_reason` values; if a belief is wrong, the fake and the code are wrong
  together and the tests still pass. **This is the mechanism's blind spot and it
  is the reason a real call is worth making at least once per change to the
  client** — see the Stage 3 summary for what was actually run against the live
  API.
- **The double lives in application code.** It ships in the image (a few hundred
  bytes, never constructed outside `test`), and it is a class whose only purpose
  is testing sitting next to one whose purpose is production.
- **A test can still construct `AnthropicClient` under a non-test profile** by
  building `Settings(environment="dev", ...)` explicitly. `test_llm.py` does this
  to assert construction succeeds — it dials nothing (no request is made until
  `stream`), but the door exists.
- **No contract test against the real API.** If Anthropic changes a field name,
  nothing in CI notices. A periodic, explicitly-opted-in contract test is the
  right answer; not built this stage.
