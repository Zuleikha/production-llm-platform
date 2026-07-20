"""The span-export contract — **retired in Stage 5, not implemented as drawn.**

Stage 1 sketched an in-house ABC here::

    class SpanExporter(ABC):
        def export(self, name: str, attributes: Mapping[str, object]) -> None: ...

Stage 5 did not build it, and the reason is worth stating rather than leaving as
a silently vanished stub. **OpenTelemetry's SDK already defines this contract**,
and defines it better than the sketch did:

- It exports a **batch** of ``ReadableSpan``s, not one name-and-attributes at a
  time. The sketch's signature has no place to put a parent, a trace id, a start
  or end time, or a status — so it cannot express a *trace*, only an isolated
  event. It is a logging call wearing a span's name.
- It returns a ``SpanExportResult``, so a caller can retry. The sketch returns
  ``None``: a failed export is indistinguishable from a successful one.
- Every exporter worth having — OTLP, Jaeger, console, in-memory — already
  implements it. An in-house ABC would mean writing an adapter for each, to reach
  a worse interface.

So the seam this service owns is not an exporter at all. It is
:func:`~services.monitoring.tracing.build_tracer_provider`: *which* provider —
and therefore which exporter — the active profile is allowed to construct. That
is the decision this codebase actually needs to own, because it is the one
keeping the test suite off the network (ADR 0016). The exporter behind it is
OTel's, and ``SpanExporter`` is re-exported below so that the name in this
package's public surface since Stage 1 now resolves to the real contract instead
of a rival one.

This is the same call as Stage 3 dropping ``langchain`` rather than promoting it
(ADR 0006), and Stage 4 preferring ``llama-index-core``'s primitives to its own
(ADR 0011): where a standard already fits, adopt it — do not wrap it in a
homegrown interface that only loses information.
"""

from __future__ import annotations

from opentelemetry.sdk.trace.export import SpanExporter as SpanExporter

__all__ = ["SpanExporter"]
