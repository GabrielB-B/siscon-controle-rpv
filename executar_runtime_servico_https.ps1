param(
    [string]$ServerIp = "",
    [int]$Port = 8443
)

$ErrorActionPreference = "Stop"

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

$projectRoot = (Resolve-Path $PSScriptRoot).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Python da runtime nao encontrado em $python"
}

$resolvedServerIp = Resolve-ServerIp -PreferredIp $ServerIp
$certDir = Join-Path $projectRoot "instance\certs"
$serverCert = Join-Path $certDir "controle_rpv_local.crt"
$serverKey = Join-Path $certDir "controle_rpv_local.key"

Push-Location $projectRoot
try {
    if (-not (Test-Path $serverCert) -or -not (Test-Path $serverKey)) {
        Write-Host "Gerando certificado HTTPS local para a runtime..." -ForegroundColor Cyan
        $args = @(
            ".\gerar_certificado_https_local.py",
            "--cert-dir", $certDir,
            "--host", $resolvedServerIp,
            "--host", "localhost",
            "--host", "127.0.0.1",
            "--host", $env:COMPUTERNAME
        )
        & $python @args
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
    }

    Write-Host "Aplicando migrations da runtime..." -ForegroundColor Cyan
    & $python -m flask db upgrade heads
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    $env:APP_HOST = "0.0.0.0"
    $env:APP_PORT = [string]$Port
    $env:SESSION_COOKIE_SECURE = "1"
    $env:HTTPS_CERT_FILE = $serverCert
    $env:HTTPS_KEY_FILE = $serverKey

    Write-Host "Subindo runtime HTTPS em https://$resolvedServerIp`:$Port" -ForegroundColor Green
    & $python .\serve_https_local.py
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}

