"""Cross-cutting Prometheus metrics that do not belong to a single service layer.

The HTTP request counter/histogram live in ``services/api/metrics.py`` — they are
the ``api`` service's own RED metrics. The two counters here are different: they
are reliability *events* (Stage 9, ADR 0020) raised from layers the ``api``
package must not import (``services/orchestrator`` and ``services/security`` both
sit *below* the API in the dependency graph, so importing an ``api`` module from
them would invert the arrow). They live in ``shared/`` for the same reason
``@traced`` does — everything is allowed to import ``shared``.

Both are registered against the default ``prometheus_client`` registry at import,
so ``/metrics`` exposes them as soon as the module is imported (which the app does
transitively at startup, via the circuit breaker and the rate limiter). They are
counters of *edges*, not gauges: each records the moment a reliability boundary
was crossed, which is exactly what the two Stage 9 alert rules threshold against.

These are NOT a second copy of the app's RED metrics — that duplication is what
ADR 0016 refused. They are new signals with no existing source (ADR 0020).
"""

from __future__ import annotations

from prometheus_client import Counter

# Incremented once each time a circuit breaker transitions into the OPEN state
# (the edge, not once per rejected request). Labelled by breaker name so more
# than one wrapped resource stays distinguishable. Drives the "circuit breaker
# open" alert (ADR 0020): an open breaker is a caller-visible new failure mode —
# the provider is being treated as down and chat requests fail fast with 503.
CIRCUIT_BREAKER_OPENED = Counter(
    "circuit_breaker_opened_total",
    "Times a circuit breaker transitioned into the open state.",
    labelnames=("name",),
)

# Incremented every time the rate limiter fails open on a Redis outage and allows
# a request it could not count (ADR 0008/0019). The decision to fail open is not
# up for debate (a limiter outage must not become an endpoint outage); this metric
# instruments it so the fail-open path firing is *visible* rather than silent, and
# drives the "rate limiter fail-open" alert (ADR 0020).
RATE_LIMITER_FAIL_OPEN = Counter(
    "rate_limiter_fail_open_total",
    "Times the rate limiter failed open on a Redis outage and allowed the request.",
)
