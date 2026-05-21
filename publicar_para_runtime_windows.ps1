[CmdletBinding()]
param(
    [string]$RuntimePath = "",
    [switch]$SkipRequirementsInstall,
    [switch]$ForceSeedStateFromSource
)

$ErrorActionPreference = "Stop"

function Get-DefaultRuntimePath {
    param([string]$SourcePath)

    $leaf = Split-Path -Leaf $SourcePath
    if ($leaf -like "*_runtime") {
        throw "Este script deve ser executado a partir da pasta de desenvolvimento, nao da runtime."
    }

    $parent = Split-Path -Parent $SourcePath
    return Join-Path $parent ("{0}_runtime" -f $leaf)
}

function Assert-RobocopySuccess {
    param([int]$ExitCode)

    if ($ExitCode -ge 8) {
        throw "Robocopy falhou com codigo $ExitCode."
    }
}

function Copy-ItemIfMissingOrForced {
    param(
        [string]$SourcePath,
        [string]$DestinationPath,
        [switch]$Recurse,
        [switch]$ForceMode
    )

    if (-not (Test-Path $SourcePath)) {
        return $false
    }

    if ((Test-Path $DestinationPath) -and -not $ForceMode) {
        return $false
    }

    $destinationParent = Split-Path -Parent $DestinationPath
    if ($destinationParent) {
        New-Item -ItemType Directory -Path $destinationParent -Force | Out-Null
    }

    if ((Test-Path $DestinationPath) -and $ForceMode) {
        Remove-Item -LiteralPath $DestinationPath -Recurse:$Recurse -Force
    }

    Copy-Item -LiteralPath $SourcePath -Destination $DestinationPath -Recurse:$Recurse
    return $true
}

function Get-SourceTreeMetadata {
    param([string]$SourcePath)

    $metadata = [ordered]@{
        git_commit = ""
        git_branch = ""
        git_dirty  = $null
    }

    try {
        $commit = (& git -C $SourcePath rev-parse --short HEAD 2>$null | Out-String).Trim()
        if ($LASTEXITCODE -eq 0) {
            $metadata.git_commit = $commit
        }
    }
    catch {}

    try {
        $branch = (& git -C $SourcePath branch --show-current 2>$null | Out-String).Trim()
        if ($LASTEXITCODE -eq 0) {
            $metadata.git_branch = $branch
        }
    }
    catch {}

    try {
        $status = (& git -C $SourcePath status --short 2>$null | Out-String).Trim()
        if ($LASTEXITCODE -eq 0) {
            $metadata.git_dirty = -not [string]::IsNullOrWhiteSpace($status)
        }
    }
    catch {}

    return $metadata
}

function Get-SuspiciousSourceArtifacts {
    param([string]$SourcePath)

    $artifacts = New-Object System.Collections.Generic.List[string]
    $explicitTargets = @(
        "tests\instance",
        ".pytest_cache",
        ".tmp_pytest"
    )

    foreach ($relativePath in $explicitTargets) {
        $candidatePath = Join-Path $SourcePath $relativePath
        if (Test-Path $candidatePath) {
            $artifacts.Add($candidatePath)
        }
    }

    Get-ChildItem -LiteralPath $SourcePath -Force -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like "pytest-cache-files-*" -or $_.Name -like "tmp*" } |
        ForEach-Object { $artifacts.Add($_.FullName) }

    return $artifacts.ToArray()
}

function Copy-SqliteDbConsistently {
    param(
        [string]$PythonExe,
        [string]$SourceDbPath,
        [string]$DestinationDbPath
    )

    if (-not (Test-Path $PythonExe)) {
        throw "Python nao encontrado para copiar o banco SQLite com seguranca."
    }

    $destinationParent = Split-Path -Parent $DestinationDbPath
    if ($destinationParent) {
        New-Item -ItemType Directory -Path $destinationParent -Force | Out-Null
    }

    $copyScript = @'
import sqlite3
import sys

source_db, destination_db = sys.argv[1], sys.argv[2]
with sqlite3.connect(source_db) as source:
    with sqlite3.connect(destination_db) as destination:
        source.backup(destination)
'@

    & $PythonExe -c $copyScript $SourceDbPath $DestinationDbPath
    if ($LASTEXITCODE -ne 0) {
        throw "Falha ao copiar o banco SQLite para a runtime."
    }
}

function Remove-ExplicitRuntimeArtifacts {
    param([string]$RuntimePath)

    $removedTargets = New-Object System.Collections.Generic.List[string]
    $explicitTargets = @(
        ".git",
        "docs",
        "tests",
        ".pytest_cache",
        ".tmp_pytest"
    )
    $rootFilePatterns = @(
        "*.xlsx",
        "*.xls",
        "*.pdf"
    )

    foreach ($relativePath in $explicitTargets) {
        $targetPath = Join-Path $RuntimePath $relativePath
        if (Test-Path $targetPath) {
            Remove-Item -LiteralPath $targetPath -Recurse -Force
            $removedTargets.Add($targetPath)
        }
    }

    Get-ChildItem -LiteralPath $RuntimePath -Force -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like "pytest-cache-files-*" } |
        ForEach-Object {
            Remove-Item -LiteralPath $_.FullName -Recurse -Force
            $removedTargets.Add($_.FullName)
        }

    Get-ChildItem -LiteralPath $RuntimePath -Force -File -ErrorAction SilentlyContinue |
        Where-Object {
            foreach ($pattern in $rootFilePatterns) {
                if ($_.Name -like $pattern) {
                    return $true
                }
            }
            return $false
        } |
        ForEach-Object {
            Remove-Item -LiteralPath $_.FullName -Force
            $removedTargets.Add($_.FullName)
        }

    return $removedTargets.ToArray()
}

function Write-PublishManifest {
    param(
        [string]$ManifestPath,
        [string]$SourcePath,
        [string]$RuntimePath,
        [hashtable]$SourceTreeMetadata,
        [bool]$SkipRequirementsInstall,
        [bool]$ForceSeedStateFromSource,
        [string[]]$ExcludedDirectories,
        [string[]]$ExcludedFiles,
        [string[]]$SuspiciousArtifacts,
        [string[]]$RemovedRuntimeArtifacts,
        [string[]]$StateMessages
    )

    $manifest = [ordered]@{
        published_at                  = (Get-Date).ToString("s")
        source_path                   = $SourcePath
        runtime_path                  = $RuntimePath
        git_commit                    = $SourceTreeMetadata.git_commit
        git_branch                    = $SourceTreeMetadata.git_branch
        git_dirty                     = $SourceTreeMetadata.git_dirty
        skip_requirements_install     = $SkipRequirementsInstall
        force_seed_state_from_source  = $ForceSeedStateFromSource
        excluded_directories          = $ExcludedDirectories
        excluded_files                = $ExcludedFiles
        suspicious_source_artifacts   = $SuspiciousArtifacts
        removed_runtime_artifacts     = $RemovedRuntimeArtifacts
        state_messages                = $StateMessages
    }

    $manifestParent = Split-Path -Parent $ManifestPath
    if ($manifestParent) {
        New-Item -ItemType Directory -Path $manifestParent -Force | Out-Null
    }

    $manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $ManifestPath -Encoding UTF8
}

$sourcePath = (Resolve-Path $PSScriptRoot).Path
if (-not $RuntimePath) {
    $RuntimePath = Get-DefaultRuntimePath -SourcePath $sourcePath
}

$RuntimePath = [System.IO.Path]::GetFullPath($RuntimePath)
$sourcePython = Join-Path $sourcePath ".venv\Scripts\python.exe"
$runtimePython = Join-Path $RuntimePath ".venv\Scripts\python.exe"
$runtimeEnv = Join-Path $RuntimePath ".env"
$sourceEnv = Join-Path $sourcePath ".env"
$runtimeEnvExample = Join-Path $RuntimePath ".env.example"
$sourceInstancePath = Join-Path $sourcePath "instance"
$runtimeInstancePath = Join-Path $RuntimePath "instance"
$sourceDbPath = Join-Path $sourceInstancePath "controle_rpv.db"
$runtimeDbPath = Join-Path $runtimeInstancePath "controle_rpv.db"
$sourceTreeMetadata = Get-SourceTreeMetadata -SourcePath $sourcePath
$suspiciousSourceArtifacts = Get-SuspiciousSourceArtifacts -SourcePath $sourcePath

Write-Host "Origem (dev): $sourcePath" -ForegroundColor Cyan
Write-Host "Destino (runtime): $RuntimePath" -ForegroundColor Cyan

if ($sourceTreeMetadata.git_commit) {
    Write-Host "Commit atual: $($sourceTreeMetadata.git_commit)" -ForegroundColor Cyan
}
if ($sourceTreeMetadata.git_branch) {
    Write-Host "Branch atual: $($sourceTreeMetadata.git_branch)" -ForegroundColor Cyan
}
if ($null -ne $sourceTreeMetadata.git_dirty) {
    $dirtyLabel = if ($sourceTreeMetadata.git_dirty) { "sim" } else { "nao" }
    Write-Host "Worktree com mudancas locais: $dirtyLabel" -ForegroundColor Cyan
}
if ($suspiciousSourceArtifacts.Count -gt 0) {
    Write-Host "Artefatos locais nao operacionais detectados na dev e ignorados na publicacao:" -ForegroundColor Yellow
    foreach ($artifactPath in $suspiciousSourceArtifacts) {
        Write-Host "- $artifactPath" -ForegroundColor Yellow
    }
}

New-Item -ItemType Directory -Path $RuntimePath -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $RuntimePath "instance") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $RuntimePath "backups") -Force | Out-Null

$excludedDirectories = @(
    ".git",
    "docs",
    ".venv",
    "instance",
    "backups",
    ".pytest_cache",
    ".vscode",
    ".idea",
    "entrada",
    "saida",
    "__pycache__",
    "pytest-cache-files-*",
    "tmp*",
    ".tmp_pytest",
    "tests",
    "htmlcov",
    ".mypy_cache",
    ".ruff_cache"
)
$excludedFiles = @(
    ".git",
    ".env",
    "*.db",
    "*.sqlite",
    "*.sqlite3",
    "*.log",
    "*.xlsx",
    "*.xls",
    "*.pdf",
    ".coverage*",
    "coverage.xml"
)

$robocopyArgs = @(
    $sourcePath,
    $RuntimePath,
    "/MIR",
    "/XD",
    $excludedDirectories,
    "/XF",
    $excludedFiles
)

$stateMessages = @()
foreach ($artifactPath in $suspiciousSourceArtifacts) {
    $stateMessages += "Artefato local ignorado na publicacao: $artifactPath"
}

Write-Host "Sincronizando codigo para a runtime..." -ForegroundColor Cyan
& robocopy @robocopyArgs | Out-Host
Assert-RobocopySuccess -ExitCode $LASTEXITCODE

$removedRuntimeArtifacts = Remove-ExplicitRuntimeArtifacts -RuntimePath $RuntimePath
foreach ($removedTarget in $removedRuntimeArtifacts) {
    $stateMessages += "Artefato nao operacional removido da runtime: $removedTarget"
}

if (-not (Test-Path $runtimeEnv)) {
    if (Test-Path $sourceEnv) {
        Copy-Item -LiteralPath $sourceEnv -Destination $runtimeEnv
        Write-Host "Arquivo .env inicial copiado da pasta dev para a runtime." -ForegroundColor Yellow
    }
    elseif (Test-Path $runtimeEnvExample) {
        Copy-Item -LiteralPath $runtimeEnvExample -Destination $runtimeEnv
        Write-Host "Arquivo .env inicial criado a partir de .env.example na runtime." -ForegroundColor Yellow
    }
    else {
        Write-Host "A runtime ainda nao tem .env. Ajuste esse arquivo antes de subir o sistema." -ForegroundColor Yellow
    }
}
else {
    Write-Host "Arquivo .env da runtime preservado." -ForegroundColor Green
}

if ((Test-Path $sourceDbPath) -and ((-not (Test-Path $runtimeDbPath)) -or $ForceSeedStateFromSource)) {
    if ($ForceSeedStateFromSource -and (Test-Path $runtimeDbPath)) {
        Write-Host "Sobrescrevendo o banco da runtime com o estado atual da pasta dev." -ForegroundColor Yellow
    }

    Copy-SqliteDbConsistently -PythonExe $sourcePython -SourceDbPath $sourceDbPath -DestinationDbPath $runtimeDbPath
    $stateMessages += "Banco SQLite da runtime sincronizado a partir da pasta dev."
}

$copyTargets = @(
    @{
        Source = Join-Path $sourceInstancePath "admin_bootstrap_password.txt"
        Destination = Join-Path $runtimeInstancePath "admin_bootstrap_password.txt"
        Recurse = $false
    },
    @{
        Source = Join-Path $sourceInstancePath "certs"
        Destination = Join-Path $runtimeInstancePath "certs"
        Recurse = $true
    },
    @{
        Source = Join-Path $sourceInstancePath "notifications"
        Destination = Join-Path $runtimeInstancePath "notifications"
        Recurse = $true
    },
    @{
        Source = Join-Path $sourceInstancePath "import_previews"
        Destination = Join-Path $runtimeInstancePath "import_previews"
        Recurse = $true
    }
)

foreach ($copyTarget in $copyTargets) {
    $copied = Copy-ItemIfMissingOrForced `
        -SourcePath $copyTarget.Source `
        -DestinationPath $copyTarget.Destination `
        -Recurse:$copyTarget.Recurse `
        -ForceMode:$ForceSeedStateFromSource

    if ($copied) {
        $stateMessages += "Estado runtime atualizado: $($copyTarget.Destination)"
    }
}

if (-not (Test-Path $runtimePython)) {
    Write-Host "Criando ambiente virtual da runtime..." -ForegroundColor Cyan

    if (Test-Path $sourcePython) {
        & $sourcePython -m venv (Join-Path $RuntimePath ".venv")
    }
    else {
        $pyCommand = Get-Command py.exe -ErrorAction SilentlyContinue
        if (-not $pyCommand) {
            throw "Nao foi possivel criar a .venv da runtime. Python nao encontrado nem na .venv atual nem em py.exe."
        }

        & py -3 -m venv (Join-Path $RuntimePath ".venv")
    }
}

$runtimePython = Join-Path $RuntimePath ".venv\Scripts\python.exe"
if (-not (Test-Path $runtimePython)) {
    throw "Python da runtime nao encontrado em $runtimePython"
}

if (-not $SkipRequirementsInstall) {
    $requirements = Join-Path $RuntimePath "requirements.txt"
    if (Test-Path $requirements) {
        Write-Host "Instalando dependencias na runtime..." -ForegroundColor Cyan
        & $runtimePython -m pip install -r $requirements
        if ($LASTEXITCODE -ne 0) {
            throw "Falha ao instalar dependencias da runtime."
        }
    }
}

$manifestTimestamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
$manifestDir = Join-Path $RuntimePath "backups"
$timestampedManifestPath = Join-Path $manifestDir ("publish_manifest_{0}.json" -f $manifestTimestamp)
$latestManifestPath = Join-Path $manifestDir "last_publish_manifest.json"

Write-PublishManifest `
    -ManifestPath $timestampedManifestPath `
    -SourcePath $sourcePath `
    -RuntimePath $RuntimePath `
    -SourceTreeMetadata $sourceTreeMetadata `
    -SkipRequirementsInstall ([bool]$SkipRequirementsInstall) `
    -ForceSeedStateFromSource ([bool]$ForceSeedStateFromSource) `
    -ExcludedDirectories $excludedDirectories `
    -ExcludedFiles $excludedFiles `
    -SuspiciousArtifacts $suspiciousSourceArtifacts `
    -RemovedRuntimeArtifacts $removedRuntimeArtifacts `
    -StateMessages $stateMessages

Write-PublishManifest `
    -ManifestPath $latestManifestPath `
    -SourcePath $sourcePath `
    -RuntimePath $RuntimePath `
    -SourceTreeMetadata $sourceTreeMetadata `
    -SkipRequirementsInstall ([bool]$SkipRequirementsInstall) `
    -ForceSeedStateFromSource ([bool]$ForceSeedStateFromSource) `
    -ExcludedDirectories $excludedDirectories `
    -ExcludedFiles $excludedFiles `
    -SuspiciousArtifacts $suspiciousSourceArtifacts `
    -RemovedRuntimeArtifacts $removedRuntimeArtifacts `
    -StateMessages $stateMessages

Write-Host ""
Write-Host "Runtime preparada com sucesso." -ForegroundColor Green
Write-Host "Pasta runtime: $RuntimePath" -ForegroundColor Green
Write-Host "Manifesto da publicacao: $timestampedManifestPath" -ForegroundColor Green
if ($stateMessages.Count -gt 0) {
    Write-Host "Estado inicial da runtime:" -ForegroundColor Cyan
    foreach ($message in $stateMessages) {
        Write-Host "- $message"
    }
}
Write-Host ""
Write-Host "Proximos passos sugeridos:" -ForegroundColor Cyan
Write-Host "1. Revise o .env em: $runtimeEnv"
Write-Host "2. Gere backup na runtime antes de publicar alteracoes futuras."
Write-Host "3. Para instalar o servico do Windows, rode:" 
Write-Host "   .\instalar_servico_runtime_windows.ps1 -RuntimePath `"$RuntimePath`" -ServerIp SEU_IP"
