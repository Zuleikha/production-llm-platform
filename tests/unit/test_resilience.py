"""Tests for the generic circuit breaker (shared/resilience.py, ADR 0020).

These drive the state machine directly with a controllable clock, so the
closed → open → half-open → closed path and its timing are exercised without any
sleeping or any dependency on the wrapped resource. The LLM wrapper that uses this
breaker is tested separately in ``test_llm.py``.
"""

from __future__ import annotations

import pytest
from shared.metrics import CIRCUIT_BREAKER_OPENED
from shared.resilience import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitState,
)


class Boom(Exception):
    """A qualifying failure for these tests."""


class NotQualifying(Exception):
    """An exception the breaker is configured to ignore."""


class _Clock:
    """A hand-cranked monotonic clock."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _breaker(clock: _Clock, *, threshold: int = 3, cooldown: float = 30.0) -> CircuitBreaker:
    return CircuitBreaker(
        name="test",
        failure_threshold=threshold,
        cooldown_seconds=cooldown,
        trip_on=(Boom,),
        clock=clock,
    )


def _opened_count(name: str) -> float:
    """Current value of the open-transition counter for ``name``."""
    return float(CIRCUIT_BREAKER_OPENED.labels(name)._value.get())


class TestConstruction:
    def test_rejects_a_zero_threshold(self) -> None:
        with pytest.raises(ValueError, match="failure_threshold"):
            CircuitBreaker(name="x", failure_threshold=0, cooldown_seconds=1, trip_on=(Boom,))

    def test_rejects_a_non_positive_cooldown(self) -> None:
        with pytest.raises(ValueError, match="cooldown_seconds"):
            CircuitBreaker(name="x", failure_threshold=1, cooldown_seconds=0, trip_on=(Boom,))

    def test_starts_closed(self) -> None:
        assert _breaker(_Clock()).state is CircuitState.CLOSED


class TestTripping:
    def test_stays_closed_below_the_threshold(self) -> None:
        breaker = _breaker(_Clock(), threshold=3)

        breaker.record_failure(Boom())
        breaker.record_failure(Boom())

        assert breaker.state is CircuitState.CLOSED
        breaker.before_call()  # does not raise

    def test_opens_on_the_threshold_failure(self) -> None:
        breaker = _breaker(_Clock(), threshold=3)

        for _ in range(3):
            breaker.record_failure(Boom())

        assert breaker.state is CircuitState.OPEN
        with pytest.raises(CircuitBreakerOpenError, match="is open"):
            breaker.before_call()

    def test_a_success_resets_the_consecutive_count(self) -> None:
        breaker = _breaker(_Clock(), threshold=3)

        breaker.record_failure(Boom())
        breaker.record_failure(Boom())
        breaker.record_success()
        breaker.record_failure(Boom())
        breaker.record_failure(Boom())

        # Four failures total but never three in a row — still closed.
        assert breaker.state is CircuitState.CLOSED

    def test_a_non_qualifying_exception_does_not_count(self) -> None:
        breaker = _breaker(_Clock(), threshold=2)

        breaker.record_failure(NotQualifying())
        breaker.record_failure(NotQualifying())
        breaker.record_failure(NotQualifying())

        assert breaker.state is CircuitState.CLOSED


class TestCooldownAndRecovery:
    def test_stays_open_until_the_cooldown_elapses(self) -> None:
        clock = _Clock()
        breaker = _breaker(clock, threshold=1, cooldown=30)

        breaker.record_failure(Boom())
        clock.advance(29)

        with pytest.raises(CircuitBreakerOpenError):
            breaker.before_call()

    def test_moves_to_half_open_after_the_cooldown(self) -> None:
        clock = _Clock()
        breaker = _breaker(clock, threshold=1, cooldown=30)

        breaker.record_failure(Boom())
        clock.advance(30)
        breaker.before_call()  # the single trial is allowed through

        assert breaker.state is CircuitState.HALF_OPEN

    def test_a_successful_trial_closes_the_breaker(self) -> None:
        clock = _Clock()
        breaker = _breaker(clock, threshold=1, cooldown=30)

        breaker.record_failure(Boom())
        clock.advance(30)
        breaker.before_call()
        breaker.record_success()

        assert breaker.state is CircuitState.CLOSED

    def test_a_failed_trial_re_opens_immediately(self) -> None:
        clock = _Clock()
        breaker = _breaker(clock, threshold=1, cooldown=30)

        breaker.record_failure(Boom())
        clock.advance(30)
        breaker.before_call()
        breaker.record_failure(Boom())  # trial failed

        assert breaker.state is CircuitState.OPEN
        # The cooldown restarts from the re-open, not the first open.
        clock.advance(29)
        with pytest.raises(CircuitBreakerOpenError):
            breaker.before_call()


class TestMetric:
    def test_opening_increments_the_counter_once_per_edge(self) -> None:
        clock = _Clock()
        breaker = CircuitBreaker(
            name="metric-test",
            failure_threshold=1,
            cooldown_seconds=30,
            trip_on=(Boom,),
            clock=clock,
        )
        before = _opened_count("metric-test")

        breaker.record_failure(Boom())  # open (edge 1)
        # Rejected calls while open must NOT re-increment.
        clock.advance(1)
        with pytest.raises(CircuitBreakerOpenError):
            breaker.before_call()

        assert _opened_count("metric-test") == before + 1
