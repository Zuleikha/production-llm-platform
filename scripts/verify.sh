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

# --- Security scanners (mirror the CI gitleaks + pip-audit gates; ADR 0019) ------
# gitleaks: CI scans a clean checkout (tracked files only, no .env/.venv/gl). The
# repo's .gitleaks.toml deliberately does NOT allowlist .env, so a naive `gitleaks
# dir .` here would false-fail on a developer's real gitignored .env. Reproduce the
# CI surface instead by scanning a copy of the *tracked* files with their current
# working-tree content: excludes .env/.venv/gl naturally, still catches a secret in
# a committed or staged file before it reaches CI. Skipped (not failed) if gitleaks
# is not installed -- CI remains the hard enforcer.
gitleaks_bin=""
if command -v gitleaks >/dev/null 2>&1; then gitleaks_bin=gitleaks
elif [ -x gl/gitleaks ]; then gitleaks_bin=./gl/gitleaks
elif [ -x gl/gitleaks.exe ]; then gitleaks_bin=./gl/gitleaks.exe
fi
if [ -n "$gitleaks_bin" ]; then
  echo "==> Secret scan (gitleaks)"
  scan_dir="$(mktemp -d)"
  trap 'rm -rf "$scan_dir"' EXIT
  while IFS= read -r -d '' f; do
    [ -e "$f" ] || continue
    mkdir -p "$scan_dir/$(dirname "$f")"
    cp "$f" "$scan_dir/$f"
  done < <(git ls-files -z)
  "$gitleaks_bin" dir "$scan_dir" --config .gitleaks.toml --redact --no-banner --exit-code 1
  rm -rf "$scan_dir"
  trap - EXIT
else
  echo "==> Secret scan (gitleaks) - SKIPPED (binary not on PATH or in gl/); CI still enforces it."
fi

# pip-audit: audit exactly what is locked (--frozen), mirroring CI.
echo "==> Dependency scan (pip-audit)"
req="$(mktemp)"
uv export --frozen --no-emit-project --format requirements-txt >"$req"
uvx --from pip-audit==2.9.0 pip-audit -r "$req"
rm -f "$req"

echo
echo "All checks passed."
