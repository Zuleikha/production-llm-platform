# monitoring — interface stub

> **Status: partially scaffolded; application export planned.** Delivered in
> **Stage 5 (observability)**.

## What already exists in Stage 1

- Prometheus + Grafana run as containers (`docker-compose.yml`).
- The API exposes `/metrics` (request count + latency).
- A dependency-free `@traced` decorator (`shared/observability.py`) logs
  structured span enter/exit/error events.

## Intended contract (this stub)

`SpanExporter` (see `base.py`) is the seam for exporting application spans to a
backend.

| Method | Signature | Meaning |
|--------|-----------|---------|
| `export` | `export(name: str, attributes: Mapping[str, object]) -> None` | Export one span. |

## Planned scope (Stage 5)

- **OpenTelemetry** SDK export (traces + metrics) via a collector.
- Grafana dashboards + alerting rules provisioned as code.
- `@traced` extended to emit real OTel spans (call sites unchanged).

`export` currently raises `NotImplementedError`.
