"""Generic resilience primitives — a circuit breaker.

Cross-cutting infrastructure, not LLM-specific, so it lives in ``shared/``: any
call that can fail transiently (a model provider, a datastore driver, a future
outbound HTTP hop) can be wrapped without importing ``services/``. Stage 9 uses it
around the Anthropic ``LLMClient`` (ADR 0020); the primitive itself knows nothing
about that.

The breaker has three states:

- **closed** — calls pass through. Consecutive *qualifying* failures are counted;
  a success resets the count.
- **open** — the failure threshold was reached. Calls fail fast with
  :class:`CircuitBreakerOpenError` without touching the wrapped resource, until a
  cooldown elapses. This is the whole point: when the provider is down, stop
  spending each request's full timeout budget rediscovering that, and stop piling
  new calls onto a resource that cannot serve them.
- **half-open** — the cooldown has elapsed and exactly one trial call is allowed.
  Its success closes the breaker; its failure re-opens it for another cooldown.

**Only qualifying exceptions count.** ``trip_on`` names the exception types that
mean "the resource is down" — for the Anthropic client, transport/5xx failures.
A caller-side error (the API's 400 for a bad request shape, ADR 0006) is *not*
the provider being down; opening the breaker over it would take the whole endpoint
down for a request-shape mistake, so a non-qualifying exception passes straight
through with the breaker state untouched.

This does **not** replace the Anthropic SDK's own transient-error retries — it
sits above them (ADR 0020). The SDK retries an individual call; the breaker stops
the flood of calls once retries are clearly not helping.
"""

from __future__ import annotations

import time
from enum import StrEnum
from typing import TYPE_CHECKING

from shared.logging import get_logger
from shared.metrics import CIRCUIT_BREAKER_OPENED
from shared.observability import traced

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

_logger = get_logger("shared.resilience")


class CircuitState(StrEnum):
    """The three states a :class:`CircuitBreaker` moves between."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpenError(RuntimeError):
    """Raised when a call is attempted while the breaker is open.

    Carries only the breaker's ``name`` — a static identifier, never any call
    data — so it is safe to log and to render into an error envelope (the API
    maps it to a ``503 provider_unavailable``, ADR 0020).
    """

    def __init__(self, name: str) -> None:
        super().__init__(f"circuit breaker {name!r} is open")
        self.name = name


class CircuitBreaker:
    """A consecutive-failure circuit breaker with a timed cooldown.

    Not thread-safe and not async-aware by design: it holds a few integers and a
    timestamp and mutates them synchronously. The asyncio event loop it runs under
    gives single-threaded, run-to-completion execution between ``await`` points,
    and every method here is synchronous, so two coroutines cannot interleave
    inside one. That keeps the primitive trivially correct without a lock.
    """

    def __init__(
        self,
        *,
        name: str,
        failure_threshold: int,
        cooldown_seconds: float,
        trip_on: Iterable[type[BaseException]],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if cooldown_seconds <= 0:
            raise ValueError("cooldown_seconds must be > 0")
        self._name = name
        self._threshold = failure_threshold
        self._cooldown = cooldown_seconds
        # A tuple so it can be handed straight to isinstance().
        self._trip_on = tuple(trip_on)
        self._clock = clock
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at: float | None = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def state(self) -> CircuitState:
        return self._state

    @traced
    def before_call(self) -> None:
        """Gate a call. Raise :class:`CircuitBreakerOpenError` if the breaker is open.

        Transitions open → half-open once the cooldown has elapsed, letting the
        next call through as the single trial. Called immediately before the
        wrapped resource is touched.
        """
        if self._state is CircuitState.OPEN:
            assert self._opened_at is not None  # OPEN always records when it opened
            if self._clock() - self._opened_at >= self._cooldown:
                self._transition(CircuitState.HALF_OPEN)
            else:
                raise CircuitBreakerOpenError(self._name)

    @traced
    def record_success(self) -> None:
        """Register a successful call: close a half-open breaker, reset the count."""
        if self._state is CircuitState.HALF_OPEN:
            self._transition(CircuitState.CLOSED)
        self._consecutive_failures = 0

    @traced
    def record_failure(self, exc: BaseException) -> None:
        """Register a failed call. Non-qualifying exceptions leave the state untouched.

        A qualifying failure in half-open re-opens immediately (the trial failed);
        in closed it increments the consecutive count and opens on the threshold.
        """
        if not isinstance(exc, self._trip_on):
            # Not "the resource is down" — a 400, a caller bug. Don't count it,
            # and don't reset the count either: it is simply not evidence.
            return
        if self._state is CircuitState.HALF_OPEN:
            self._open()
            return
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._threshold:
            self._open()

    def _open(self) -> None:
        self._opened_at = self._clock()
        self._transition(CircuitState.OPEN)

    def _transition(self, new_state: CircuitState) -> None:
        old_state = self._state
        if old_state is new_state:
            return
        self._state = new_state
        if new_state is CircuitState.CLOSED:
            self._consecutive_failures = 0
            self._opened_at = None
        # State transitions carry only the breaker name and the two state labels —
        # no call data — so this log is clean by construction (CLAUDE.md).
        _logger.warning(
            "circuit_breaker.state_change",
            extra={"breaker": self._name, "from": old_state.value, "to": new_state.value},
        )
        if new_state is CircuitState.OPEN:
            CIRCUIT_BREAKER_OPENED.labels(self._name).inc()
