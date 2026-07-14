"""Query the foundation endpoints of a running api service.

Usage:
    docker compose up -d --build
    uv run python examples/check_health.py

Override the target with BASE_URL, e.g. BASE_URL=http://localhost:8000
"""

from __future__ import annotations

import os
import sys

import httpx

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")


def main() -> int:
    """Print the response of each foundation endpoint. Returns a shell exit code."""
    try:
        with httpx.Client(base_url=BASE_URL, timeout=5.0) as client:
            for path in ("/health", "/ready", "/version"):
                response = client.get(path)
                response.raise_for_status()
                print(f"{path:<10} {response.status_code}  {response.json()}")

            metrics = client.get("/metrics")
            metrics.raise_for_status()
            series = sum(
                1 for line in metrics.text.splitlines() if line.startswith("http_requests_total{")
            )
            print(f"{'/metrics':<10} {metrics.status_code}  http_requests_total series: {series}")
    except httpx.HTTPError as exc:
        print(f"ERROR: could not reach {BASE_URL}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
