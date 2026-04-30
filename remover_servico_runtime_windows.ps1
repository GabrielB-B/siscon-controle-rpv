[CmdletBinding()]
param(
    [string]$TaskName = "ControleRPVRuntimeHTTPS"
)

$ErrorActionPreference = "Stop"

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-IsAdministrator)) {
    throw "Execute este script em PowerShell como administrador."
}

$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Tarefa $TaskName removida." -ForegroundColor Green
}
else {
    Write-Host "Tarefa $TaskName nao encontrada." -ForegroundColor Yellow
}

$legacyService = Get-Service -Name $TaskName -ErrorAction SilentlyContinue
if ($legacyService) {
    if ($legacyService.Status -ne "Stopped") {
        Stop-Service -Name $TaskName -Force -ErrorAction SilentlyContinue
    }
    & sc.exe delete $TaskName | Out-Host
    Write-Host "Servico legado $TaskName removido." -ForegroundColor Green
}
