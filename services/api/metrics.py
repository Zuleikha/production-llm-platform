"""Prometheus metric definitions for the API service.

Metrics are registered against the default ``prometheus_client`` registry at
import time (module-level singletons) so they survive repeated app creation in
tests without duplicate-registration errors.
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram

REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests processed.",
    labelnames=("method", "path", "status"),
)

REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds.",
    labelnames=("method", "path"),
)
