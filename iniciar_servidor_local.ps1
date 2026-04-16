$ErrorActionPreference = "Stop"

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Error "Ambiente virtual nao encontrado em .venv\\Scripts\\python.exe"
}

Push-Location $PSScriptRoot
try {
    Write-Host "Aplicando migrations..." -ForegroundColor Cyan
    & $python -m flask db upgrade
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    if (-not $env:APP_HOST) {
        $env:APP_HOST = "0.0.0.0"
    }

    if (-not $env:APP_PORT) {
        $env:APP_PORT = "8080"
    }

    if (-not $env:APP_THREADS) {
        $env:APP_THREADS = "8"
    }

    Write-Host "Subindo servidor local em rede..." -ForegroundColor Green
    & $python .\serve_local.py
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
