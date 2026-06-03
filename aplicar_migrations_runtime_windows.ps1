$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path $PSScriptRoot).Path
$projectFolder = Split-Path $projectRoot -Leaf

if ($projectFolder -ne "controle_rpv_runtime") {
    throw "Este script aplica migrations da RUNTIME e deve ser executado somente em C:\Users\gabriel.bispo\Documents\controle_rpv_runtime."
}

$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Python da runtime nao encontrado em $python"
}

Push-Location $projectRoot
try {
    Write-Host "Revision atual da runtime:" -ForegroundColor Cyan
    & $python -m flask db current
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    Write-Host "Heads disponiveis:" -ForegroundColor Cyan
    & $python -m flask db heads
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    Write-Host "Aplicando migrations da runtime por acao explicita..." -ForegroundColor Cyan
    & $python -m flask db upgrade heads
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    Write-Host "Revision final da runtime:" -ForegroundColor Green
    & $python -m flask db current
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
