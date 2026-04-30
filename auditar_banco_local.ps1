$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
$scriptPath = Join-Path $projectRoot "auditar_banco_local.py"

if (-not (Test-Path $pythonExe)) {
    throw "Python do ambiente virtual nao encontrado em $pythonExe"
}

& $pythonExe $scriptPath
