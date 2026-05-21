param(
    [string]$ServerIp,
    [int]$Port = 8445,
    [switch]$ForceCert
)

$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path $PSScriptRoot).Path
$projectFolder = Split-Path $projectRoot -Leaf

if ($projectFolder -like "*runtime*") {
    throw "Este script e para DEV. Caminho atual: $projectRoot. Para runtime, use C:\Users\gabriel.bispo\Documents\controle_rpv_runtime\executar_runtime_servico_https.ps1"
}

if ($Port -eq 8443) {
    throw "Porta 8443 e reservada para a runtime. Suba a dev em 8445: .\iniciar_servidor_https_local.ps1 -ServerIp <IP> -Port 8445 -ForceCert"
}

$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Error "Ambiente virtual nao encontrado em .venv\\Scripts\\python.exe"
}

if (-not $ServerIp) {
    $ServerIp = Get-NetIPAddress -AddressFamily IPv4 |
        Where-Object { $_.IPAddress -notlike "169.254*" -and $_.IPAddress -ne "127.0.0.1" } |
        Select-Object -First 1 -ExpandProperty IPAddress
}

if (-not $ServerIp) {
    Write-Error "Nao foi possivel identificar o IP local. Informe -ServerIp manualmente."
}

$certDir = Join-Path $projectRoot "instance\certs"
$serverCert = Join-Path $certDir "controle_rpv_local.crt"
$serverKey = Join-Path $certDir "controle_rpv_local.key"
$rootCert = Join-Path $certDir "controle_rpv_local_ca.crt"
$ruleName = "Controle RPV HTTPS $Port"

Push-Location $projectRoot
try {
    Write-Host "Aplicando migrations..." -ForegroundColor Cyan
    & $python -m flask db upgrade heads
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    Write-Host "Gerando certificado HTTPS local..." -ForegroundColor Cyan
    $args = @(
        ".\gerar_certificado_https_local.py",
        "--cert-dir", $certDir,
        "--host", $ServerIp,
        "--host", "localhost",
        "--host", "127.0.0.1",
        "--host", $env:COMPUTERNAME
    )

    if ($ForceCert) {
        $args += "--force"
    }

    & $python @args
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    if (-not (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue)) {
        Write-Host "Criando regra de firewall para a porta $Port..." -ForegroundColor Cyan
        New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port -Profile Domain | Out-Null
    }

    $env:APP_HOST = "0.0.0.0"
    $env:APP_PORT = [string]$Port
    $env:SESSION_COOKIE_SECURE = "1"
    $env:HTTPS_CERT_FILE = $serverCert
    $env:HTTPS_KEY_FILE = $serverKey

    Write-Host ""
    Write-Host "Instale este certificado nos PCs clientes para remover o aviso do navegador:" -ForegroundColor Yellow
    Write-Host $rootCert -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Subindo servidor HTTPS em https://$ServerIp`:$Port" -ForegroundColor Green
    & $python .\serve_https_local.py
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
