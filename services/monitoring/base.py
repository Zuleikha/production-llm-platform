"""Interface stub: application observability export contract.

**Planned, not yet implemented — Stage 5 (observability).**

The infrastructure (Prometheus, Grafana) is already wired in
``docker-compose.yml``, and the API exposes ``/metrics`` in Stage 1. This
service defines the *application-level* export seam — OpenTelemetry span export,
trace/metric pipelines — that extends the Stage 1 ``@traced`` decorator. Concrete
exporters arrive in Stage 5. See ``README.md``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping


class SpanExporter(ABC):
    """Exports a completed trace span to an observability backend."""

    @abstractmethod
    def export(self, name: str, attributes: Mapping[str, object]) -> None:
        """Export one span with ``name`` and ``attributes``.

        Raises:
            NotImplementedError: Always, until implemented in Stage 5.
        """
        raise NotImplementedError
