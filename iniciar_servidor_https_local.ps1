param(
    [string]$ServerIp,
    [int]$Port = 8445,
    [switch]$ForceCert,
    [switch]$ApplyMigrations
)

$ErrorActionPreference = "Stop"

function Get-AlembicRevisionIds {
    param(
        [string]$PythonExe,
        [string[]]$AlembicArgs
    )

    $token = [guid]::NewGuid().ToString("N")
    $stdoutPath = Join-Path $env:TEMP ("alembic_stdout_{0}.log" -f $token)
    $stderrPath = Join-Path $env:TEMP ("alembic_stderr_{0}.log" -f $token)

    try {
        $arguments = @("-m", "flask", "db") + $AlembicArgs
        $process = Start-Process `
            -FilePath $PythonExe `
            -ArgumentList $arguments `
            -WorkingDirectory (Get-Location).Path `
            -NoNewWindow `
            -PassThru `
            -Wait `
            -RedirectStandardOutput $stdoutPath `
            -RedirectStandardError $stderrPath

        $outputParts = @()
        if (Test-Path $stdoutPath) {
            $stdoutText = Get-Content -LiteralPath $stdoutPath -Raw
            if (-not [string]::IsNullOrWhiteSpace($stdoutText)) {
                $outputParts += $stdoutText.Trim()
            }
        }

        if (Test-Path $stderrPath) {
            $stderrText = Get-Content -LiteralPath $stderrPath -Raw
            if (-not [string]::IsNullOrWhiteSpace($stderrText)) {
                $outputParts += $stderrText.Trim()
            }
        }

        $output = $outputParts -join "`n"
    }
    finally {
        Remove-Item -LiteralPath $stdoutPath, $stderrPath -Force -ErrorAction SilentlyContinue
    }

    if ($process.ExitCode -ne 0) {
        throw "Falha ao consultar revisions do Alembic.`n$output"
    }

    return @(
        [regex]::Matches($output, "\b[0-9a-f]{12,}\b") |
            ForEach-Object { $_.Value.ToLowerInvariant() } |
            Select-Object -Unique |
            Sort-Object
    )
}

function Assert-NoPendingMigrations {
    param(
        [string]$PythonExe,
        [string]$EnvironmentLabel,
        [string]$ApplyCommand
    )

    $current = Get-AlembicRevisionIds -PythonExe $PythonExe -AlembicArgs @("current")
    $heads = Get-AlembicRevisionIds -PythonExe $PythonExe -AlembicArgs @("heads")
    $pending = Compare-Object -ReferenceObject $current -DifferenceObject $heads

    if ($pending) {
        throw (
            "Migrations pendentes detectadas para $EnvironmentLabel. " +
            "A subida foi interrompida para nao alterar schema automaticamente. " +
            "Aplique conscientemente com: $ApplyCommand"
        )
    }
}

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
    if ($ApplyMigrations) {
        Write-Host "Aplicando migrations da dev por solicitacao explicita..." -ForegroundColor Cyan
        & (Join-Path $projectRoot "aplicar_migrations_dev.ps1")
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
    }
    else {
        Assert-NoPendingMigrations `
            -PythonExe $python `
            -EnvironmentLabel "dev" `
            -ApplyCommand ".\aplicar_migrations_dev.ps1"
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
