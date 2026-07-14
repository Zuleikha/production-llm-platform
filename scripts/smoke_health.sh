#!/usr/bin/env bash
# Poll the api container's /health until it returns 200 (the Stage 1
# verification bar), then check the other foundation endpoints.
#
#   docker compose up -d --build && ./scripts/smoke_health.sh
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
ATTEMPTS="${ATTEMPTS:-30}"

echo "Waiting for ${BASE_URL}/health ..."
for i in $(seq 1 "$ATTEMPTS"); do
  code="$(curl -s -o /dev/null -w '%{http_code}' "${BASE_URL}/health" || true)"
  if [ "$code" = "200" ]; then
    echo "OK: /health returned 200 after ${i}s"
    echo
    for path in /health /ready /version; do
      printf '%s -> ' "$path"
      curl -fsS "${BASE_URL}${path}"
      echo
    done
    printf '/metrics -> '
    curl -fsS "${BASE_URL}/metrics" | grep -c 'http_requests_total' >/dev/null && echo "http_requests_total present"
    exit 0
  fi
  sleep 1
done

echo "ERROR: /health did not return 200 within ${ATTEMPTS}s" >&2
docker compose logs api || true
exit 1
