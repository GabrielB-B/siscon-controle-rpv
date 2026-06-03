$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path $PSScriptRoot).Path
$projectFolder = Split-Path $projectRoot -Leaf

if ($projectFolder -like "*runtime*") {
    throw "Este script e para DEV. Caminho atual: $projectRoot. Para runtime, use aplicar_migrations_runtime_windows.ps1 na pasta runtime."
}

$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Python da dev nao encontrado em $python"
}

Push-Location $projectRoot
try {
    Write-Host "Revision atual da dev:" -ForegroundColor Cyan
    & $python -m flask db current
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    Write-Host "Heads disponiveis:" -ForegroundColor Cyan
    & $python -m flask db heads
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    Write-Host "Aplicando migrations da dev..." -ForegroundColor Cyan
    & $python -m flask db upgrade heads
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    Write-Host "Revision final da dev:" -ForegroundColor Green
    & $python -m flask db current
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
