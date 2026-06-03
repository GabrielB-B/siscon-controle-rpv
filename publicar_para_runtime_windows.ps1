[CmdletBinding()]
param(
    [string]$RuntimePath = "",
    [switch]$SkipRequirementsInstall,
    [switch]$InitializeRuntimeEnvironment,
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

function Get-PortableRelativePath {
    param(
        [string]$BasePath,
        [string]$TargetPath
    )

    $baseFullPath = [System.IO.Path]::GetFullPath($BasePath)
    $targetFullPath = [System.IO.Path]::GetFullPath($TargetPath)

    if (-not $baseFullPath.EndsWith([System.IO.Path]::DirectorySeparatorChar)) {
        $baseFullPath += [System.IO.Path]::DirectorySeparatorChar
    }

    $baseUri = New-Object System.Uri($baseFullPath)
    $targetUri = New-Object System.Uri($targetFullPath)
    return [System.Uri]::UnescapeDataString($baseUri.MakeRelativeUri($targetUri).ToString()).Replace("/", "\")
}

function Ensure-RuntimeScaffold {
    param([string]$RuntimePath)

    $paths = @(
        $RuntimePath,
        (Join-Path $RuntimePath "instance"),
        (Join-Path $RuntimePath "instance\backups"),
        (Join-Path $RuntimePath "instance\certs"),
        (Join-Path $RuntimePath "instance\import_previews"),
        (Join-Path $RuntimePath "instance\logs"),
        (Join-Path $RuntimePath "instance\notifications"),
        (Join-Path $RuntimePath "backups")
    )

    foreach ($path in $paths) {
        New-Item -ItemType Directory -Path $path -Force | Out-Null
    }
}

function Get-SourceTreeMetadata {
    param([string]$SourcePath)

    $metadata = [ordered]@{
        git_available     = $false
        git_commit_short  = ""
        git_commit_full   = ""
        git_branch        = ""
        git_dirty         = $null
        git_dirty_files   = @()
    }

    try {
        $commitFull = (& git -C $SourcePath rev-parse HEAD 2>$null | Out-String).Trim()
        if ($LASTEXITCODE -eq 0 -and $commitFull) {
            $metadata.git_available = $true
            $metadata.git_commit_full = $commitFull
        }
    }
    catch {}

    try {
        $commitShort = (& git -C $SourcePath rev-parse --short HEAD 2>$null | Out-String).Trim()
        if ($LASTEXITCODE -eq 0 -and $commitShort) {
            $metadata.git_available = $true
            $metadata.git_commit_short = $commitShort
        }
    }
    catch {}

    try {
        $branch = (& git -C $SourcePath branch --show-current 2>$null | Out-String).Trim()
        if ($LASTEXITCODE -eq 0) {
            $metadata.git_available = $true
            $metadata.git_branch = $branch
        }
    }
    catch {}

    try {
        $statusOutput = (& git -C $SourcePath status --short 2>$null | Out-String)
        if ($LASTEXITCODE -eq 0) {
            $metadata.git_available = $true
            $status = $statusOutput.Trim()
            $metadata.git_dirty = -not [string]::IsNullOrWhiteSpace($status)
            if ($metadata.git_dirty) {
                $metadata.git_dirty_files = @(
                    $status -split "`r?`n" |
                        Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
                        ForEach-Object { $_.TrimEnd() }
                )
            }
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

function Get-RuntimeStateWarnings {
    param(
        [string]$RuntimePath,
        [string]$RuntimeDbPath
    )

    $warnings = New-Object System.Collections.Generic.List[string]
    $rootDbPath = Join-Path $RuntimePath "controle_rpv.db"

    if (Test-Path $rootDbPath) {
        $rootFullPath = [System.IO.Path]::GetFullPath($rootDbPath)
        $runtimeFullPath = [System.IO.Path]::GetFullPath($RuntimeDbPath)
        if ($rootFullPath -ne $runtimeFullPath) {
            $warnings.Add(
                "Banco SQLite na raiz da runtime detectado em $rootFullPath. Revise e coloque em quarentena manual antes da proxima publicacao."
            )
        }
    }

    return $warnings.ToArray()
}

function Test-ShouldFingerprintFile {
    param(
        [System.IO.FileInfo]$File,
        [string]$BasePath
    )

    $relativePath = Get-PortableRelativePath -BasePath $BasePath -TargetPath $File.FullName
    $segments = $relativePath -split "[\\/]"
    $excludedSegmentNames = @(
        ".git",
        ".venv",
        "instance",
        "backups",
        ".pytest_cache",
        ".vscode",
        ".idea",
        "entrada",
        "saida",
        "__pycache__",
        "tests",
        "docs",
        "htmlcov",
        ".mypy_cache",
        ".ruff_cache"
    )

    foreach ($segment in $segments) {
        if ($excludedSegmentNames -contains $segment) {
            return $false
        }
    }

    $excludedFilePatterns = @(
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

    foreach ($pattern in $excludedFilePatterns) {
        if ($File.Name -like $pattern) {
            return $false
        }
    }

    return $true
}

function Get-CodeFingerprint {
    param([string]$TargetPath)

    $lines = New-Object System.Collections.Generic.List[string]

    Get-ChildItem -LiteralPath $TargetPath -Recurse -File -Force -ErrorAction SilentlyContinue |
        Where-Object { Test-ShouldFingerprintFile -File $_ -BasePath $TargetPath } |
        Sort-Object { (Get-PortableRelativePath -BasePath $TargetPath -TargetPath $_.FullName).Replace("\", "/") } |
        ForEach-Object {
            $relativePath = (Get-PortableRelativePath -BasePath $TargetPath -TargetPath $_.FullName).Replace("\", "/")
            $fileHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash.ToLowerInvariant()
            $lines.Add("$relativePath|$fileHash")
        }

    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $joined = $lines -join "`n"
        $hashBytes = $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($joined))
    }
    finally {
        $sha.Dispose()
    }

    return [ordered]@{
        algorithm  = "sha256"
        file_count = $lines.Count
        fingerprint = ([System.BitConverter]::ToString($hashBytes)).Replace("-", "").ToLowerInvariant()
    }
}

function Write-PublishManifest {
    param(
        [string]$ManifestPath,
        [string]$SourcePath,
        [string]$RuntimePath,
        [hashtable]$SourceTreeMetadata,
        [bool]$SkipRequirementsInstall,
        [bool]$InitializeRuntimeEnvironment,
        [string[]]$ExcludedDirectories,
        [string[]]$ExcludedFiles,
        [string[]]$SuspiciousArtifacts,
        [string[]]$RemovedRuntimeArtifacts,
        [string[]]$StateMessages,
        [hashtable]$CodeFingerprint,
        [bool]$RuntimeEnvPresent,
        [bool]$RuntimeDatabasePresent
    )

    $manifest = [ordered]@{
        published_at                  = (Get-Date).ToString("s")
        publish_mode                  = if ($InitializeRuntimeEnvironment) { "initialize_runtime_environment" } else { "code_only_publish" }
        publish_policy                = "codigo_apenas_sem_copiar_banco_sem_copiar_env_dev_sem_semeadura_de_estado"
        source_path                   = $SourcePath
        runtime_path                  = $RuntimePath
        git_available                 = $SourceTreeMetadata.git_available
        git_commit_short              = $SourceTreeMetadata.git_commit_short
        git_commit_full               = $SourceTreeMetadata.git_commit_full
        git_branch                    = $SourceTreeMetadata.git_branch
        git_dirty                     = $SourceTreeMetadata.git_dirty
        git_dirty_files               = $SourceTreeMetadata.git_dirty_files
        skip_requirements_install     = $SkipRequirementsInstall
        initialize_runtime_environment = $InitializeRuntimeEnvironment
        runtime_env_present           = $RuntimeEnvPresent
        runtime_database_present      = $RuntimeDatabasePresent
        runtime_code_fingerprint      = $CodeFingerprint
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

if ($ForceSeedStateFromSource) {
    throw (
        "A flag -ForceSeedStateFromSource foi bloqueada por seguranca. " +
        "Use backup, restauracao controlada e checkpoint explicito para qualquer recuperacao especial de banco."
    )
}

$RuntimePath = [System.IO.Path]::GetFullPath($RuntimePath)
$sourcePython = Join-Path $sourcePath ".venv\Scripts\python.exe"
$runtimePython = Join-Path $RuntimePath ".venv\Scripts\python.exe"
$runtimeEnv = Join-Path $RuntimePath ".env"
$runtimeEnvExample = Join-Path $RuntimePath ".env.example"
$runtimeInstancePath = Join-Path $RuntimePath "instance"
$runtimeDbPath = Join-Path $runtimeInstancePath "controle_rpv.db"
$sourceTreeMetadata = Get-SourceTreeMetadata -SourcePath $sourcePath
$suspiciousSourceArtifacts = Get-SuspiciousSourceArtifacts -SourcePath $sourcePath
$runtimeExisted = Test-Path $RuntimePath
$stateMessages = @()

Write-Host "Origem (dev): $sourcePath" -ForegroundColor Cyan
Write-Host "Destino (runtime): $RuntimePath" -ForegroundColor Cyan

if ($sourceTreeMetadata.git_commit_short) {
    Write-Host "Commit atual: $($sourceTreeMetadata.git_commit_short)" -ForegroundColor Cyan
}
if ($sourceTreeMetadata.git_branch) {
    Write-Host "Branch atual: $($sourceTreeMetadata.git_branch)" -ForegroundColor Cyan
}
if ($null -ne $sourceTreeMetadata.git_dirty) {
    $dirtyLabel = if ($sourceTreeMetadata.git_dirty) { "sim" } else { "nao" }
    Write-Host "Worktree com mudancas locais: $dirtyLabel" -ForegroundColor Cyan
}
if ($sourceTreeMetadata.git_dirty_files.Count -gt 0) {
    Write-Host "Arquivos locais nao commitados detectados:" -ForegroundColor Yellow
    foreach ($dirtyFile in $sourceTreeMetadata.git_dirty_files) {
        Write-Host "- $dirtyFile" -ForegroundColor Yellow
    }
}
if ($suspiciousSourceArtifacts.Count -gt 0) {
    Write-Host "Artefatos locais nao operacionais detectados na dev e ignorados na publicacao:" -ForegroundColor Yellow
    foreach ($artifactPath in $suspiciousSourceArtifacts) {
        Write-Host "- $artifactPath" -ForegroundColor Yellow
    }
}

if (-not $runtimeExisted -and -not $InitializeRuntimeEnvironment) {
    throw (
        "A pasta runtime ainda nao existe em $RuntimePath. " +
        "Use .\\inicializar_runtime_ambiente_windows.ps1 para preparar a runtime pela primeira vez."
    )
}

if (-not $runtimeExisted -and $InitializeRuntimeEnvironment) {
    Write-Host "Inicializando scaffold seguro da runtime..." -ForegroundColor Cyan
    $stateMessages += "Scaffold inicial da runtime criado sem copiar banco, .env da dev ou estado sensivel."
}

Ensure-RuntimeScaffold -RuntimePath $RuntimePath

if ((-not $InitializeRuntimeEnvironment) -and (-not (Test-Path $runtimeEnv))) {
    throw (
        "Arquivo .env da runtime ausente em $runtimeEnv. " +
        "Restaure o .env a partir de backup ou rode .\\inicializar_runtime_ambiente_windows.ps1 para um bootstrap controlado."
    )
}

if ((-not $InitializeRuntimeEnvironment) -and (-not (Test-Path $runtimeDbPath))) {
    throw (
        "Banco da runtime ausente em $runtimeDbPath. " +
        "A publicacao de codigo foi interrompida para nao semear banco da dev. " +
        "Restaure o banco por backup antes de publicar."
    )
}

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
    if (-not $InitializeRuntimeEnvironment) {
        throw "Arquivo .env da runtime continua ausente apos a publicacao."
    }

    if (-not (Test-Path $runtimeEnvExample)) {
        throw (
            "Nao foi possivel criar o .env inicial da runtime porque .env.example nao existe em $runtimeEnvExample."
        )
    }

    Copy-Item -LiteralPath $runtimeEnvExample -Destination $runtimeEnv
    Write-Host "Arquivo .env inicial criado a partir de .env.example na runtime." -ForegroundColor Yellow
    $stateMessages += "Arquivo .env inicial criado a partir de .env.example. Revise manualmente antes de subir a runtime."
}
else {
    Write-Host "Arquivo .env da runtime preservado." -ForegroundColor Green
}

if (Test-Path $runtimeDbPath) {
    Write-Host "Banco da runtime preservado." -ForegroundColor Green
}
elseif ($InitializeRuntimeEnvironment) {
    Write-Host "Banco da runtime nao foi criado automaticamente." -ForegroundColor Yellow
    $stateMessages += "Banco da runtime permanece ausente. Aplique migrations conscientemente antes da primeira subida."
}
else {
    throw "Banco da runtime ausente apos a publicacao."
}

foreach ($warning in (Get-RuntimeStateWarnings -RuntimePath $RuntimePath -RuntimeDbPath $runtimeDbPath)) {
    Write-Host $warning -ForegroundColor Yellow
    $stateMessages += $warning
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
$codeFingerprint = Get-CodeFingerprint -TargetPath $RuntimePath

Write-PublishManifest `
    -ManifestPath $timestampedManifestPath `
    -SourcePath $sourcePath `
    -RuntimePath $RuntimePath `
    -SourceTreeMetadata $sourceTreeMetadata `
    -SkipRequirementsInstall ([bool]$SkipRequirementsInstall) `
    -InitializeRuntimeEnvironment ([bool]$InitializeRuntimeEnvironment) `
    -ExcludedDirectories $excludedDirectories `
    -ExcludedFiles $excludedFiles `
    -SuspiciousArtifacts $suspiciousSourceArtifacts `
    -RemovedRuntimeArtifacts $removedRuntimeArtifacts `
    -StateMessages $stateMessages `
    -CodeFingerprint $codeFingerprint `
    -RuntimeEnvPresent ([bool](Test-Path $runtimeEnv)) `
    -RuntimeDatabasePresent ([bool](Test-Path $runtimeDbPath))

Write-PublishManifest `
    -ManifestPath $latestManifestPath `
    -SourcePath $sourcePath `
    -RuntimePath $RuntimePath `
    -SourceTreeMetadata $sourceTreeMetadata `
    -SkipRequirementsInstall ([bool]$SkipRequirementsInstall) `
    -InitializeRuntimeEnvironment ([bool]$InitializeRuntimeEnvironment) `
    -ExcludedDirectories $excludedDirectories `
    -ExcludedFiles $excludedFiles `
    -SuspiciousArtifacts $suspiciousSourceArtifacts `
    -RemovedRuntimeArtifacts $removedRuntimeArtifacts `
    -StateMessages $stateMessages `
    -CodeFingerprint $codeFingerprint `
    -RuntimeEnvPresent ([bool](Test-Path $runtimeEnv)) `
    -RuntimeDatabasePresent ([bool](Test-Path $runtimeDbPath))

Write-Host ""
Write-Host "Runtime preparada com sucesso." -ForegroundColor Green
Write-Host "Modo: $(if ($InitializeRuntimeEnvironment) { 'bootstrap seguro' } else { 'publicacao de codigo' })" -ForegroundColor Green
Write-Host "Pasta runtime: $RuntimePath" -ForegroundColor Green
Write-Host "Manifesto da publicacao: $timestampedManifestPath" -ForegroundColor Green
Write-Host "Fingerprint do codigo publicado: $($codeFingerprint.fingerprint)" -ForegroundColor Green
if ($stateMessages.Count -gt 0) {
    Write-Host "Estado da runtime:" -ForegroundColor Cyan
    foreach ($message in $stateMessages) {
        Write-Host "- $message"
    }
}
Write-Host ""
Write-Host "Proximos passos sugeridos:" -ForegroundColor Cyan
if ($InitializeRuntimeEnvironment) {
    Write-Host "1. Revise o .env em: $runtimeEnv"
    Write-Host "2. Aplique migrations conscientemente: .\aplicar_migrations_runtime_windows.ps1"
    Write-Host "3. Para instalar o servico do Windows, rode:"
    Write-Host "   .\instalar_servico_runtime_windows.ps1 -RuntimePath `"$RuntimePath`" -ServerIp SEU_IP"
}
else {
    Write-Host "1. Confirme backup recente do banco da runtime."
    Write-Host "2. Se houver migration aprovada, aplique conscientemente: .\aplicar_migrations_runtime_windows.ps1"
    Write-Host "3. Reinicie a tarefa/servico da runtime de forma controlada."
}
