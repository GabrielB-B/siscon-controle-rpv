param(
    [string]$ServerIp = "",
    [int]$Port = 8443,
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
$projectFolder = Split-Path $projectRoot -Leaf

if ($projectFolder -ne "controle_rpv_runtime") {
    throw "Este script inicia a RUNTIME e deve ser executado somente em C:\Users\gabriel.bispo\Documents\controle_rpv_runtime. Caminho atual: $projectRoot. Para DEV, use .\iniciar_servidor_https_local.ps1 -Port 8445."
}

if ($Port -ne 8443) {
    throw "A runtime deve continuar na porta 8443 para preservar o link operacional anterior. Comando correto: .\executar_runtime_servico_https.ps1 -ServerIp <IP> -Port 8443"
}

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
    if ($ApplyMigrations) {
        Write-Host "Aplicando migrations da runtime por solicitacao explicita..." -ForegroundColor Cyan
        & (Join-Path $projectRoot "aplicar_migrations_runtime_windows.ps1")
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
    }
    else {
        Assert-NoPendingMigrations `
            -PythonExe $python `
            -EnvironmentLabel "runtime" `
            -ApplyCommand ".\aplicar_migrations_runtime_windows.ps1"
    }

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
