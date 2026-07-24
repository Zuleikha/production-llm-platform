"""Locust load scenarios for the api service (Stage 9, ADR 0020).

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

The bearer key is read from the environment (``LOAD_TEST_API_KEY``) — never
hardcoded — defaulting to the ``test``-profile's fixed, obviously-fake key so the
primary mode runs with no setup. For the secondary mode, mint one with
``scripts/generate_api_key.py`` and export it.
"""

from __future__ import annotations

import os

from locust import HttpUser, between, task

# The test-profile container ships this fixed, obviously-fake key (see
# config/environments/test.env and tests/fakes.py). Overridden by the env var for
# the dev-profile secondary run. Never a real credential.
_DEFAULT_TEST_KEY = "test-key-not-a-real-secret"
_API_KEY = os.environ.get("LOAD_TEST_API_KEY", _DEFAULT_TEST_KEY)

_CHAT_BODY = {
    "model": "claude-opus-4-8",
    "messages": [{"role": "user", "content": "Summarise what a circuit breaker does in one line."}],
    "max_tokens": 64,
}


class ChatUser(HttpUser):
    """Simulates a client hitting the authenticated chat endpoint under load."""

    # A realistic think-time between turns, so N users ≈ N concurrent-ish callers
    # rather than an unbounded flood — the knob for the concurrency levels the
    # pool-tuning run in ADR 0020 cites.
    wait_time = between(1, 3)

    @property
    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {_API_KEY}"}

    @task(10)
    def chat_completion(self) -> None:
        """The paid path (echoed under the test profile). The dominant load."""
        self.client.post("/v1/chat/completions", json=_CHAT_BODY, headers=self._auth, name="chat")

    @task(1)
    def chat_stream(self) -> None:
        """The SSE path — same engine, different transport."""
        body = {**_CHAT_BODY, "stream": True}
        self.client.post("/v1/chat/completions", json=body, headers=self._auth, name="chat-stream")


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
