# Service Level Objectives

> **Where this lives and why.** SLOs are a distinct, list-shaped operational
> artifact — targets plus the alert that watches each one — so they get their own
> file rather than a section buried in `docs/architecture.md` (which describes how
> the system is built, not what it promises). `architecture.md` links here.

**Stage 9 (Reliability), ADR 0020.** This platform has **no real production
traffic**, so an "SLO" here is a *stated target* (availability, latency) backed by
a Grafana alert rule — the same symptom-level style as Stage 5's three rules — not
a measured historical achievement. The number is the line we would defend; the
alert is what tells us we crossed it. When real traffic exists, these become
error-budget-backed and the thresholds get revisited against it.

All alert rules are provisioned as code under
`infrastructure/docker/grafana/provisioning/alerting/` (Grafana-native unified
alerting; no Alertmanager container — ADR 0016).

---

## Objectives

| # | SLI (what we measure) | SLO (the target) | Alert rule | Severity |
|---|------------------------|------------------|------------|----------|
| 1 | 5xx rate on all API requests | < 5% of requests over any 5m window | `api-error-rate` (Stage 5) | critical |
| 2 | p99 latency of `POST /v1/chat/completions` | < 30s | `api-chat-p99-latency` (Stage 5) | warning |
| 3 | `/ready` availability | never returns 503 (any occurrence pages) | `api-not-ready` (Stage 5) | critical |
| 4 | Model-provider availability (via the breaker) | breaker stays closed | `reliability-circuit-breaker-open` (Stage 9) | critical |
| 5 | Rate-limit enforcement integrity | limiter never fails open | `reliability-rate-limiter-fail-open` (Stage 9) | warning |

Objectives 1–3 are Stage 5's, **unchanged** (`alerting/api-rules.yml`). Objectives
4–5 are new this stage (`alerting/reliability-rules.yml`), covering failure modes
Stage 9 introduced or newly instrumented.

---

## The two new rules (Stage 9)

Both fire on a **counter of edges** exposed on the API's own `/metrics`
(`shared/metrics.py`) and scraped by the existing `api` Prometheus job. The
expression `increase(...[5m]) > 0` means "did this boundary get crossed at all":
there is no healthy background rate of either event, so any occurrence is signal.

### 4. Circuit breaker open — `reliability-circuit-breaker-open`

- **Fires when:** `sum(increase(circuit_breaker_opened_total{job="api"}[5m])) > 0`,
  held for `1m`.
- **Why it matters:** an open breaker means the platform is treating the Anthropic
  provider as down — every chat request fails fast with `503 provider_unavailable`
  (ADR 0020). That is a caller-visible outage of the one paid endpoint, and it is
  a *new* failure mode Stage 5's rules do not catch (a fast 503 is neither a
  5xx-rate spike nor a latency spike). **critical.**
- **Why `1m`:** rides out a single open → half-open → closed self-recovery; a
  breaker still open after a minute is a real provider incident, not a blip.

### 5. Rate limiter fail-open — `reliability-rate-limiter-fail-open`

- **Fires when:** `sum(increase(rate_limiter_fail_open_total{job="api"}[5m])) > 0`,
  held for `5m`.
- **Why it matters:** the limiter fails open on a Redis outage *by design* — a
  limiter outage must not become an endpoint outage (ADR 0008/0019), and this
  stage does **not** reverse that. But while it fires, requests are not being
  capped, so an abusive caller is temporarily uncapped. Worth knowing; not an
  outage. **warning.**
- **Why `5m`:** distinguishes a sustained Redis outage from a one-off reconnect.

---

## Deliberately not alerted on

Consistent with Stage 5's symptom-only stance: pool saturation, cache hit-rate,
and context-window compaction events are **diagnosis**, not symptoms — they belong
on the dashboard next to the trace that explains an incident, not in a page. If
pool saturation ever *causes* a symptom (objective 1 or 2 breaching), that rule
fires and the pool metric is where you look, not a separate page at 3am.
