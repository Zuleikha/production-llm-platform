# Run the full local quality gate (the same checks CI enforces).
#   powershell -ExecutionPolicy Bypass -File scripts/verify.ps1
# Exits non-zero on the first failure.

$ErrorActionPreference = "Stop"
$env:UV_LINK_MODE = "copy"   # required: uv cache and repo are on different drives

function Invoke-Step {
    param([string]$Name, [scriptblock]$Command)
    Write-Host "==> $Name" -ForegroundColor Cyan
    & $Command
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAILED: $Name" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

Invoke-Step "Sync dependencies"  { uv sync --frozen }
Invoke-Step "Lint (ruff)"        { uv run ruff check . }
Invoke-Step "Format (ruff)"      { uv run ruff format --check . }
Invoke-Step "Type check (mypy)"  { uv run mypy }
Invoke-Step "Tests (pytest)"     { uv run pytest }

Write-Host "`nAll checks passed." -ForegroundColor Green
