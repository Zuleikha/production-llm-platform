# Observability

## The three signals

**Logs** are events, not sentences. Log one structured object per line with a
stable event name and fields alongside it, so the log can be queried rather than
grepped. `user.login_failed` with a `reason` field is a datum; "Failed to log in
user because the password was wrong" is prose that happens to be machine-
readable.

**Metrics** are aggregates. They answer "how many, how often, how long" across
all requests, and they cost the same whether one request or a million produced
them. They cannot answer "what happened to *this* request".

**Traces** answer that. A trace follows one request across service boundaries and
shows where its time went.

Reach for the cheapest signal that can answer the question. A metric that tells
you error rate is up is worth more than a log search that eventually tells you
the same thing.

## What never goes in a log

No credentials, no tokens, no personal data. This includes the obvious cases and
the ones that arrive by accident: a whole request body logged "for debugging", an
exception whose message interpolates the query it failed on, a URL with a session
token in the query string. Logs are copied, shipped, indexed and retained; a
secret in a log is a secret in every one of those places, and rotating it is the
only remedy.

## Cardinality

Do not put an unbounded value in a metric label. A user id, a request id, or a
raw URL path as a label will produce one time series per distinct value, and the
metrics backend will fall over long before you notice. Bucket the value, or put
it in a log or a trace, where high cardinality is the point.

## Alerting

Alert on symptoms customers feel, not on causes. "Error rate above 2% for five
minutes" is a symptom. "CPU above 80%" is a cause, and it is a bad alert:
sometimes high CPU is exactly what a healthy service under load looks like, and
sometimes a service is broken while idle.

Every alert must be actionable and must say what to do. An alert nobody acts on
trains everyone to ignore the next one.
