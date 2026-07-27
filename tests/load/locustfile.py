"""Locust load scenarios for the api service (Stage 9, ADR 0020; harness fix Stage 10).

**Not part of the hermetic pytest suite.** It lives under ``tests/load/`` (not
``tests/unit/``) and its filename is ``locustfile.py`` (not ``test_*.py``), so
pytest never collects it, and ``locust`` is not a locked project dependency — it
is installed transiently for a run (see ``tests/load/README.md``). Load testing
needs a *running stack*, exactly like the live-datastore / live-Qdrant /
live-provider tests that already skip by default; it is opt-in and never a CI
gate.

Two modes, both driven by this one file (ADR 0020):

1. **Primary, cost-free** — against a ``test``-profile container. The scripted
   ``LLMClient`` echoes, so there is no model bill; the load exercises real HTTP
   concurrency and real Postgres/Redis/Qdrant contention, which is what pool
   tuning, the rate-limiter fail-open path and the circuit-breaker mechanics
   actually need.
2. **Secondary, opt-in, billable** — against a ``dev``-profile container with a
   real ``ANTHROPIC_API_KEY``, for real end-to-end latency. A short, small run.
   **Never in CI.** See the README for the exact command.

Stage 10 harness fix (ADR 0020, addendum 3)
--------------------------------------------
The original harness had every simulated user authenticate as the same single
``test-principal`` and send a payload with no ``conversation_id`` that never
triggered a tool. Consequences: all users collided on **one** rate-limit bucket
and 429'd almost immediately (so requests rarely reached the pools at all), and
the Postgres conversation-cache path was never touched. This version:

* **Distinct principals.** In the cost-free Mode 1 (no ``LOAD_TEST_API_KEY`` in
  the environment), each simulated ``ChatUser`` authenticates as one of
  :data:`_NUM_LOAD_PRINCIPALS` distinct principals (``loaduser-0`` …
  ``loaduser-{N-1}``), so load spreads across N rate-limit buckets instead of
  hammering one. The raw keys are deterministic and follow the repo's committed
  fake-credential convention (``test-raw-key-…``, allowlisted in ``.gitleaks.toml``);
  their stored hashes live in ``config/environments/test.env`` (mirrored into
  ``docker-compose.test.yml`` — the drift guard is
  ``tests/unit/test_load_profile_compose.py``).
* **A stateful subset.** Roughly half the ``ChatUser``s carry a stable
  per-user ``conversation_id`` for the whole run, so the Postgres load/append
  path and its Redis read-through cache (ADR 0008) are actually exercised under
  load. A Locust user runs one request at a time, so a user's own turns are
  serialised — this never trips the ADR 0008 per-conversation concurrency gap.
* **A document-search subset.** A share of chat prompts are phrased to make the
  agent call ``document_search`` (and hit Qdrant's query pool). **Note the
  cost-free ceiling:** under Mode 1 the scripted ``LLMClient`` echoes and never
  emits a ``tool_use`` block, so these prompts only actually reach Qdrant's
  *query* pool under Mode 2 (a real model). Under Mode 1 Qdrant's pool still sees
  the readiness-probe traffic (``/ready`` → ``get_collections``) that
  :class:`ProbeUser` drives. Making the scripted client tool-call on a phrase
  would be new orchestrator logic, out of this portfolio stage's scope.

The bearer key handling is unchanged for Mode 2: set ``LOAD_TEST_API_KEY`` to a
single minted ``dev``-profile key and every user uses it (a dev stack has one
principal, so bucket-spreading does not apply there).
"""

from __future__ import annotations

import os
import random
import uuid

from locust import HttpUser, between, task

# The test-profile container ships this fixed, obviously-fake key (see
# config/environments/test.env and tests/fakes.py). Used as the Mode 2 default and
# whenever LOAD_TEST_API_KEY is unset AND a single-key path is wanted. Never real.
_DEFAULT_TEST_KEY = "test-key-not-a-real-secret"

# Mode 2 (billable dev-profile) override: one minted key, used by every user.
_ENV_KEY = os.environ.get("LOAD_TEST_API_KEY")

# Mode 1 (cost-free) distinct principals. Their stored hashes are committed in
# config/environments/test.env as loaduser-{i}:<hash>. The raw keys are rebuilt
# here by the same deterministic rule the hashes were minted from — they carry the
# `test-raw-key-` marker so `.gitleaks.toml` allowlists them (a real key would still
# trip the gate). Keep _NUM_LOAD_PRINCIPALS in sync with test.env.
_NUM_LOAD_PRINCIPALS = 20


def _load_principal_key(index: int) -> str:
    """The raw bearer key for cost-free load principal ``loaduser-{index}``."""
    return f"test-raw-key-loaduser-{index}"


# Prompts that do NOT invite a tool call — the dominant, echo-friendly load.
_PLAIN_PROMPTS = (
    "Summarise what a circuit breaker does in one line.",
    "Give me a one-sentence definition of idempotency.",
    "What is a readiness probe, briefly?",
    "Explain backpressure in one sentence.",
)

# Prompts phrased to make a real model reach for `document_search` (Mode 2 only —
# see the module docstring on why Mode 1's scripted client never tool-calls).
_DOC_SEARCH_PROMPTS = (
    "According to the platform documentation, what does the circuit breaker "
    "return to the caller when it trips?",
    "Per the project's own docs, how does the rate limiter behave when Redis is unavailable?",
    "What do the platform docs say about how retrieved document text is fenced as untrusted?",
)

# Share of chat prompts drawn from the document-search set.
_DOC_SEARCH_SHARE = 0.25
# Share of ChatUsers that carry a stable per-user conversation_id.
_STATEFUL_SHARE = 0.5

_MAX_TOKENS = 64


class ChatUser(HttpUser):
    """Simulates a client hitting the authenticated chat endpoint under load."""

    # A realistic think-time between turns, so N users ≈ N concurrent-ish callers
    # rather than an unbounded flood — the knob for the concurrency levels the
    # pool-tuning run in ADR 0020 cites.
    wait_time = between(1, 3)

    def on_start(self) -> None:
        """Pin this user's identity and statefulness once, for the whole run.

        In Mode 2 (``LOAD_TEST_API_KEY`` set) every user shares that one key. In
        Mode 1 each user takes one of the N distinct load principals so the load
        spreads across N rate-limit buckets instead of one.
        """
        if _ENV_KEY:
            self._key = _ENV_KEY
        else:
            self._key = _load_principal_key(random.randrange(_NUM_LOAD_PRINCIPALS))
        # Roughly half the users are stateful: a stable conversation_id exercises
        # the Postgres load/append path + its Redis cache under load.
        self._conversation_id: str | None = (
            str(uuid.uuid4()) if random.random() < _STATEFUL_SHARE else None
        )

    @property
    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._key}"}

    def _body(self, *, stream: bool = False) -> dict[str, object]:
        if random.random() < _DOC_SEARCH_SHARE:
            content = random.choice(_DOC_SEARCH_PROMPTS)
        else:
            content = random.choice(_PLAIN_PROMPTS)
        body: dict[str, object] = {
            "model": "claude-opus-4-8",
            "messages": [{"role": "user", "content": content}],
            "max_tokens": _MAX_TOKENS,
        }
        if self._conversation_id is not None:
            body["conversation_id"] = self._conversation_id
        if stream:
            body["stream"] = True
        return body

    @task(10)
    def chat_completion(self) -> None:
        """The paid path (echoed under the test profile). The dominant load."""
        self.client.post("/v1/chat/completions", json=self._body(), headers=self._auth, name="chat")

    @task(1)
    def chat_stream(self) -> None:
        """The SSE path — same engine, different transport."""
        self.client.post(
            "/v1/chat/completions",
            json=self._body(stream=True),
            headers=self._auth,
            name="chat-stream",
        )


class ProbeUser(HttpUser):
    """Hammers the unauthenticated liveness/readiness probes.

    Separate user class so the probe load and the chat load can be weighted
    independently on the command line, and so a datastore going down mid-run (the
    chaos runbook) shows up as /ready flipping to 503 under real traffic.
    """

    wait_time = between(0.5, 1.5)

    @task(3)
    def health(self) -> None:
        self.client.get("/health", name="health")

    @task(1)
    def ready(self) -> None:
        # 503 is an EXPECTED response during a chaos run (a datastore is down), so
        # it is not marked a failure — the point is to observe the flip, not fail
        # the load test on it.
        with self.client.get("/ready", name="ready", catch_response=True) as response:
            if response.status_code in (200, 503):
                response.success()
