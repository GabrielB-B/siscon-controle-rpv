[CmdletBinding()]
param(
    [string]$RuntimePath = "",
    [string]$TaskName = "ControleRPVRuntimeHTTPS",
    [string]$ServerIp = "",
    [int]$Port = 8443,
    [switch]$StartNow
)

$ErrorActionPreference = "Stop"

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-DefaultRuntimePath {
    param([string]$BasePath)

    $leaf = Split-Path -Leaf $BasePath
    if ($leaf -like "*_runtime") {
        return $BasePath
    }

    $parent = Split-Path -Parent $BasePath
    return Join-Path $parent ("{0}_runtime" -f $leaf)
}

function Resolve-ServerIp {
    param([string]$PreferredIp)

    if ($PreferredIp) {
        return $PreferredIp
    }

    $ip = Get-NetIPAddress -AddressFamily IPv4 |
        Where-Object { $_.IPAddress -notlike "169.254*" -and $_.IPAddress -ne "127.0.0.1" } |
        Select-Object -First 1 -ExpandProperty IPAddress

    if (-not $ip) {
        throw "Nao foi possivel identificar o IP local. Informe -ServerIp explicitamente."
    }

    return $ip
}

if (-not (Test-IsAdministrator)) {
    throw "Execute este script em PowerShell como administrador."
}

$basePath = (Resolve-Path $PSScriptRoot).Path
if (-not $RuntimePath) {
    $RuntimePath = Get-DefaultRuntimePath -BasePath $basePath
}

$RuntimePath = [System.IO.Path]::GetFullPath($RuntimePath)
$runtimePython = Join-Path $RuntimePath ".venv\Scripts\python.exe"
$launcher = Join-Path $RuntimePath "executar_runtime_servico_https.ps1"
$certDir = Join-Path $RuntimePath "instance\certs"
$firewallRuleName = "Controle RPV Runtime HTTPS $Port"
$resolvedServerIp = Resolve-ServerIp -PreferredIp $ServerIp

if (-not (Test-Path $launcher)) {
    throw "Script de execucao da runtime nao encontrado em $launcher"
}

if (-not (Test-Path $runtimePython)) {
    throw "Python da runtime nao encontrado em $runtimePython. Rode antes .\publicar_para_runtime_windows.ps1"
}

Push-Location $RuntimePath
try {
    Write-Host "Gerando ou atualizando certificado HTTPS da runtime..." -ForegroundColor Cyan
    $certArgs = @(
        ".\gerar_certificado_https_local.py",
        "--cert-dir", $certDir,
        "--host", $resolvedServerIp,
        "--host", "localhost",
        "--host", "127.0.0.1",
        "--host", $env:COMPUTERNAME
    )
    & $runtimePython @certArgs
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}
finally {
    Pop-Location
}

if (-not (Get-NetFirewallRule -DisplayName $firewallRuleName -ErrorAction SilentlyContinue)) {
    Write-Host "Criando regra de firewall para a porta $Port..." -ForegroundColor Cyan
    New-NetFirewallRule `
        -DisplayName $firewallRuleName `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort $Port `
        -Profile Domain | Out-Null
}
else {
    Write-Host "Regra de firewall ja existe para a porta $Port." -ForegroundColor Green
}

$legacyService = Get-Service -Name $TaskName -ErrorAction SilentlyContinue
if ($legacyService) {
    if ($legacyService.Status -ne "Stopped") {
        Stop-Service -Name $TaskName -Force -ErrorAction SilentlyContinue
    }
    & sc.exe delete $TaskName | Out-Host
    Start-Sleep -Seconds 2
}

$powerShellExe = Join-Path $PSHOME "powershell.exe"
$actionArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$launcher`" -ServerIp `"$resolvedServerIp`" -Port $Port"
$action = New-ScheduledTaskAction -Execute $powerShellExe -Argument $actionArgs -WorkingDirectory $RuntimePath
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0)

$task = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings

$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Write-Host "Atualizando tarefa agendada existente..." -ForegroundColor Cyan
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}
else {
    Write-Host "Criando tarefa agendada de inicializacao automatica..." -ForegroundColor Cyan
}

Register-ScheduledTask -TaskName $TaskName -InputObject $task | Out-Null

if ($StartNow) {
    Write-Host "Iniciando tarefa agora..." -ForegroundColor Cyan
    Start-ScheduledTask -TaskName $TaskName
}

Write-Host ""
Write-Host "Inicializacao automatica preparada com sucesso." -ForegroundColor Green
Write-Host "Nome da tarefa: $TaskName" -ForegroundColor Green
Write-Host "Runtime: $RuntimePath" -ForegroundColor Green
Write-Host "Endereco esperado: https://$resolvedServerIp`:$Port" -ForegroundColor Green
Write-Host ""
Write-Host "Observacao:" -ForegroundColor Cyan
Write-Host "O Windows nao aceita um script PowerShell puro como servico nativo sem wrapper."
Write-Host "Por isso, este instalador usa uma tarefa agendada em startup, que sobe a runtime automaticamente quando o PC liga."
Write-Host ""
Write-Host "Para verificar a tarefa:" -ForegroundColor Cyan
Write-Host "Get-ScheduledTask -TaskName $TaskName"
Write-Host ""
Write-Host "Para iniciar manualmente depois:" -ForegroundColor Cyan
Write-Host "Start-ScheduledTask -TaskName $TaskName"
Write-Host ""
Write-Host "Para parar manualmente depois:" -ForegroundColor Cyan
Write-Host "Stop-ScheduledTask -TaskName $TaskName"
