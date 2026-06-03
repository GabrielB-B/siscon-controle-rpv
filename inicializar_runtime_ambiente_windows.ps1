[CmdletBinding()]
param(
    [string]$RuntimePath = "",
    [switch]$SkipRequirementsInstall
)

$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path $PSScriptRoot).Path
$projectFolder = Split-Path $projectRoot -Leaf

if ($projectFolder -like "*runtime*") {
    throw "Este script deve ser executado a partir da pasta de desenvolvimento, nao da runtime."
}

$publishScript = Join-Path $projectRoot "publicar_para_runtime_windows.ps1"
if (-not (Test-Path $publishScript)) {
    throw "Script de publicacao nao encontrado em $publishScript"
}

$arguments = @{
    InitializeRuntimeEnvironment = $true
    SkipRequirementsInstall = [bool]$SkipRequirementsInstall
}

if ($RuntimePath) {
    $arguments.RuntimePath = $RuntimePath
}

& $publishScript @arguments
exit $LASTEXITCODE
