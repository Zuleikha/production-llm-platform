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

# --- Security scanners (mirror the CI gitleaks + pip-audit gates; ADR 0019) ---
# gitleaks: CI scans a clean checkout (tracked files only, no .env/.venv/gl). The
# repo's .gitleaks.toml deliberately does NOT allowlist .env, so a naive "gitleaks
# dir ." here would false-fail on a real gitignored .env. Reproduce the CI surface
# by scanning a copy of the *tracked* files with their current working-tree content.
# Skipped (not failed) if gitleaks is not installed -- CI remains the hard enforcer.
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads a UTF-8 .ps1 without
# a BOM as ANSI, turning a non-ASCII dash into a smart-quote that breaks parsing.
$gitleaks = $null
if (Get-Command gitleaks -ErrorAction SilentlyContinue) { $gitleaks = "gitleaks" }
elseif (Test-Path "gl/gitleaks.exe") { $gitleaks = ".\gl\gitleaks.exe" }
elseif (Test-Path "gl/gitleaks")     { $gitleaks = ".\gl\gitleaks" }
if ($gitleaks) {
    Write-Host "==> Secret scan (gitleaks)" -ForegroundColor Cyan
    $scanDir = Join-Path ([System.IO.Path]::GetTempPath()) ("gitleaks-scan-" + [System.Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $scanDir | Out-Null
    try {
        foreach ($f in (git ls-files)) {
            if (-not (Test-Path -LiteralPath $f)) { continue }
            $dest = Join-Path $scanDir $f
            New-Item -ItemType Directory -Path (Split-Path $dest -Parent) -Force | Out-Null
            Copy-Item -LiteralPath $f -Destination $dest
        }
        & $gitleaks dir $scanDir --config .gitleaks.toml --redact --no-banner --exit-code 1
        if ($LASTEXITCODE -ne 0) { Write-Host "FAILED: Secret scan (gitleaks)" -ForegroundColor Red; exit $LASTEXITCODE }
    } finally {
        Remove-Item -Recurse -Force $scanDir -ErrorAction SilentlyContinue
    }
} else {
    Write-Host "==> Secret scan (gitleaks) - SKIPPED (binary not on PATH or in gl/); CI still enforces it." -ForegroundColor Yellow
}

# pip-audit: audit exactly what is locked (--frozen), mirroring CI.
Invoke-Step "Dependency scan (pip-audit)" {
    $req = Join-Path ([System.IO.Path]::GetTempPath()) ("requirements-audit-" + [System.Guid]::NewGuid().ToString("N") + ".txt")
    uv export --frozen --no-emit-project --format requirements-txt | Out-File -Encoding utf8 $req
    uvx --from pip-audit==2.9.0 pip-audit -r $req
    Remove-Item -Force $req -ErrorAction SilentlyContinue
}

Write-Host "`nAll checks passed." -ForegroundColor Green
