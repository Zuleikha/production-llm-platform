#!/usr/bin/env bash
# Run the full local quality gate (the same checks CI enforces).
#   ./scripts/verify.sh
# Exits non-zero on the first failure.
set -euo pipefail

export UV_LINK_MODE="${UV_LINK_MODE:-copy}"

step() {
  echo "==> $1"
  shift
  "$@"
}

step "Sync dependencies" uv sync --frozen
step "Lint (ruff)"       uv run ruff check .
step "Format (ruff)"     uv run ruff format --check .
step "Type check (mypy)" uv run mypy
step "Tests (pytest)"    uv run pytest

echo
echo "All checks passed."
