#!/usr/bin/env bash
# Scripted end-to-end demo of the running api service (Stage 10).
#
# A single repeatable walkthrough of the platform's externally-visible contract,
# run against the REAL stack — not a mock and not paraphrased output. Bring the
# stack up first (cost-free test profile, ADR 0020 Mode 1):
#
#   docker compose -f docker-compose.yml -f docker-compose.test.yml up -d --build
#   ./scripts/demo.sh
#
# It demonstrates, in order:
#   1. a normal authenticated chat call and its response shape (200);
#   2. a streaming (SSE) call and its data: frames;
#   3. a document_search / citations call (see the note in step 3 — under the
#      cost-free test profile the scripted model echoes and never tool-calls, so
#      citations come back []; a non-empty list needs a real model, Mode 2);
#   4. an unauthenticated call returning the uniform 401;
#   5. enough calls on one principal to trip the per-principal 429;
#   6. the circuit breaker (see the note in step 6 — demonstrated hermetically by
#      the fault-injection unit tests, since the test-profile client never fails).
#
# HOST override: the demo targets http://localhost:8000 by default. On a machine
# where Docker Desktop's host port-forward is flaky (empty replies — a documented
# quirk of THIS project's Windows box, see CLAUDE.md), run it from a container on
# the compose network instead and point it at the service name:
#
#   DEMO_HOST=http://api:8000 ./scripts/demo.sh
#
# The bearer key defaults to the test profile's fixed, obviously-fake key.
set -euo pipefail

HOST="${DEMO_HOST:-http://localhost:8000}"
KEY="${DEMO_API_KEY:-test-key-not-a-real-secret}"
CHAT="$HOST/v1/chat/completions"

pp() {
  # Pretty-print JSON when a tool is available; otherwise pass through untouched.
  if command -v jq >/dev/null 2>&1; then jq .
  elif command -v python >/dev/null 2>&1; then python -m json.tool
  elif command -v python3 >/dev/null 2>&1; then python3 -m json.tool
  else cat; fi
}

rule() { printf '\n=== %s ===\n' "$1"; }

rule "1. Authenticated chat (expect 200 + choices/usage/citations)"
curl -sS -X POST "$CHAT" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"claude-opus-4-8","messages":[{"role":"user","content":"In one line, what is a readiness probe?"}],"max_tokens":64}' \
  | pp

rule "2. Streaming chat (expect text/event-stream: data: frames + [DONE])"
curl -sS -N -X POST "$CHAT" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"claude-opus-4-8","messages":[{"role":"user","content":"Say hello in three words."}],"max_tokens":64,"stream":true}'

rule "3. document_search / citations (grounded turn)"
# The citations FIELD is always present (ADR 0013). Under the cost-free test
# profile the scripted LLM echoes and never emits a tool_use block, so no
# document_search runs and citations is []. Against a real model (Mode 2, dev
# profile) the same phrasing makes the agent call document_search and citations
# carries the source chunks. The wire contract is identical either way.
curl -sS -X POST "$CHAT" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"claude-opus-4-8","messages":[{"role":"user","content":"According to the platform documentation, what does the circuit breaker return when it trips?"}],"max_tokens":64}' \
  | pp

rule "4. Unauthenticated call (expect the uniform 401)"
# Capture the body and the status code separately: piping curl's -w status line
# into pp (json.tool) alongside the body makes the parser choke on the trailing
# "HTTP 401" line ("Extra data"). Append the code after a newline, split it off,
# pretty-print only the JSON, then print the status on its own line.
resp=$(curl -sS -w $'\n%{http_code}' -X POST "$CHAT" \
  -H 'Content-Type: application/json' \
  -d '{"model":"claude-opus-4-8","messages":[{"role":"user","content":"ping"}],"max_tokens":16}')
printf '%s\n' "${resp%$'\n'*}" | pp || true
printf 'HTTP %s\n' "${resp##*$'\n'}"

rule "5. Rate limiting (hammer one principal past the 60/60s limit → 429)"
# One principal, one bucket. Fire 70 quick requests and report each status; the
# first ~60 are 200, then the limiter returns 429 for the rest.
codes=""
for i in $(seq 1 70); do
  c=$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$CHAT" \
        -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
        -d '{"model":"claude-opus-4-8","messages":[{"role":"user","content":"rl"}],"max_tokens":8}')
  codes="$codes $c"
done
echo "status codes:$codes"
echo "200s: $(echo "$codes" | tr ' ' '\n' | grep -c '^200$'); 429s: $(echo "$codes" | tr ' ' '\n' | grep -c '^429$')"

rule "6. Circuit breaker"
cat <<'NOTE'
The breaker wraps the REAL AnthropicClient only; under the test profile the
scripted client never fails, so the breaker never opens here. Its contract —
open after N transport/5xx failures, fail fast with 503 provider_unavailable,
never trip on a 400, half-open recovery — is proven hermetically by:
  tests/unit/test_resilience.py   (the generic CircuitBreaker state machine)
  tests/unit/test_llm.py          (CircuitBreakingLLMClient + FaultInjectingLLMClient)
Run: uv run pytest tests/unit/test_resilience.py tests/unit/test_llm.py -q
NOTE

echo
echo "demo complete."
