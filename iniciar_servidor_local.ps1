param(
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

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Error "Ambiente virtual nao encontrado em .venv\\Scripts\\python.exe"
}

Push-Location $PSScriptRoot
try {
    if ($ApplyMigrations) {
        Write-Host "Aplicando migrations da dev por solicitacao explicita..." -ForegroundColor Cyan
        & (Join-Path $PSScriptRoot "aplicar_migrations_dev.ps1")
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

    if (-not $env:APP_HOST) {
        $env:APP_HOST = "0.0.0.0"
    }

    if (-not $env:APP_PORT) {
        $env:APP_PORT = "8080"
    }

    if (-not $env:APP_THREADS) {
        $env:APP_THREADS = "8"
    }

    Write-Host "Subindo servidor local em rede..." -ForegroundColor Green
    & $python .\serve_local.py
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
