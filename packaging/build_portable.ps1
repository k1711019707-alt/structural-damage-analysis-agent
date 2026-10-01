param(
    [string]$PythonExe = "",
    [string]$DistRoot = "",
    [string]$Version = "2.0.0",
    [string]$ActiveRagManifest = "",
    [string]$FhlPluginScript = "",
    [string]$NodeExe = "",
    [string]$SemanticModelPath = "",
    [string]$StageRoot = "",
    [switch]$ConfirmRagRebuildComplete
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\")).Path
$ProjectPython = Join-Path $ProjectRoot "YOLO11s\Scripts\python.exe"
if (-not $PythonExe) {
    if (Test-Path -LiteralPath $ProjectPython) {
        $PythonExe = $ProjectPython
    }
    else {
        $PythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($null -ne $PythonCommand) { $PythonExe = $PythonCommand.Source }
    }
}
if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw "Python interpreter not found. Pass -PythonExe or provide YOLO11s\Scripts\python.exe."
}
if (-not $ConfirmRagRebuildComplete) {
    throw "Portable build is blocked until the background production-RAG rebuild is complete. Re-run with -ConfirmRagRebuildComplete after confirming completion."
}
if ($Version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$') {
    throw "Version must be SemVer-compatible: $Version"
}

& $PythonExe -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing pinned build tools into the selected environment..."
    & $PythonExe -m pip install "pyinstaller==6.16.0" "pyinstaller-hooks-contrib==2025.10"
}

$SpecPath = Join-Path $ProjectRoot "packaging\damage_workflow_desktop.spec"
$StagePath = if ($StageRoot) { [IO.Path]::GetFullPath($StageRoot) } else { Join-Path $ProjectRoot "build\portable-stage-$Version" }
$OutputPath = if ($DistRoot) { [IO.Path]::GetFullPath($DistRoot) } else { Join-Path $ProjectRoot "dist\portable-$Version" }
$Bundle = Join-Path $OutputPath "YOLO11DamageDesktop"
$ManifestPath = if ($ActiveRagManifest) { [IO.Path]::GetFullPath($ActiveRagManifest) } else { Join-Path $ProjectRoot "knowledge_base\active_rag.json" }
if (Test-Path -LiteralPath $StagePath) {
    throw "Portable staging path already exists; choose a new -StageRoot or remove the reviewed stale staging directory: $StagePath"
}
if (Test-Path -LiteralPath $Bundle) {
    throw "Portable output bundle already exists; choose a fresh -DistRoot and preserve the reviewed release: $Bundle"
}
$stageArgs = @(
    (Join-Path $ProjectRoot "tools\portable_release.py"),
    "--project-root", $ProjectRoot,
    "--stage-root", $StagePath,
    "--active-manifest", $ManifestPath,
    "--version", $Version
)
if ($FhlPluginScript) { $stageArgs += @("--fhl-script", [IO.Path]::GetFullPath($FhlPluginScript)) }
if ($NodeExe) { $stageArgs += @("--node-exe", [IO.Path]::GetFullPath($NodeExe)) }
if ($SemanticModelPath) { $stageArgs += @("--semantic-model-path", [IO.Path]::GetFullPath($SemanticModelPath)) }
& $PythonExe @stageArgs
if ($LASTEXITCODE -ne 0) { throw "Portable resource staging failed" }

$WorkPath = Join-Path $ProjectRoot "build\pyinstaller-$Version"
$previousStage = $env:YOLO11_PORTABLE_STAGE
try {
    $env:YOLO11_PORTABLE_STAGE = $StagePath
    & $PythonExe -m PyInstaller --clean --noconfirm --workpath $WorkPath --distpath $OutputPath $SpecPath
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }
}
finally {
    if ($null -eq $previousStage) { Remove-Item Env:YOLO11_PORTABLE_STAGE -ErrorAction SilentlyContinue }
    else { $env:YOLO11_PORTABLE_STAGE = $previousStage }
}

if (-not (Test-Path -LiteralPath (Join-Path $Bundle "YOLO11DamageDesktop.exe"))) {
    throw "PyInstaller completed without the expected executable: $Bundle"
}
& $PythonExe (Join-Path $ProjectRoot "packaging\ensure_fhl_console_hidden.py") (Join-Path $Bundle "_internal\fhl_plugin\generate.mjs")
if ($LASTEXITCODE -ne 0) { throw "FHL plugin console-suppression patch failed" }
# Static import libraries are useful for compiling extensions but never loaded
# by the frozen application; removing them keeps the portable package usable.
Get-ChildItem -LiteralPath $Bundle -File -Recurse -Filter *.lib | Remove-Item -Force
Get-ChildItem -LiteralPath $Bundle -File -Recurse -Filter default.docx | Remove-Item -Force
Get-ChildItem -LiteralPath $Bundle -Directory -Recurse -Filter templates |
    Where-Object { $_.Parent.Name -eq "docx" } |
    Remove-Item -Recurse -Force
@"
@echo off
setlocal
cd /d "%~dp0"
start "YOLO11DamageDesktop" "%~dp0YOLO11DamageDesktop.exe"
"@ | Set-Content -LiteralPath (Join-Path $Bundle "启动YOLO11DamageDesktop.bat") -Encoding ASCII
$stageManifest = Get-Content -Raw -LiteralPath (Join-Path $StagePath "portable_stage_manifest.json") | ConvertFrom-Json
$manifest = [ordered]@{
    product = "YOLO11DamageDesktop"
    version = $Version
    built_at_utc = [DateTime]::UtcNow.ToString("o")
    python = (& $PythonExe --version 2>&1 | Out-String).Trim()
    entrypoint = "scripts/launch_yolo11s_seg_gui.py"
    reports = @("report.json", "report.md", "repair_plan.json", "construction_plan.json", "construction_plan.md")
    word_report_templates = @()
    knowledge_base = [ordered]@{
        mode = "bundled-active-rag"
        manifest = $stageManifest.active_rag.manifest
        database = $stageManifest.active_rag.database
        database_sha256 = $stageManifest.active_rag.database_sha256
        semantic_index = $stageManifest.active_rag.semantic_index
        semantic_index_sha256 = $stageManifest.active_rag.semantic_index_sha256
        source_documents_bundled = $stageManifest.active_rag.source_documents_bundled
    }
    semantic_model = $stageManifest.semantic_model
    fhl = $stageManifest.fhl
    files = @()
}
Get-ChildItem -LiteralPath $Bundle -File -Recurse | ForEach-Object {
    $relative = $_.FullName.Substring($Bundle.Length + 1).Replace("\", "/")
    $manifest.files += [ordered]@{ path = $relative; size_bytes = $_.Length; sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
}
$manifestJson = $manifest | ConvertTo-Json -Depth 6
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText((Join-Path $Bundle "release_manifest.json"), $manifestJson, $utf8NoBom)
& $PythonExe (Join-Path $ProjectRoot "tools\verify_portable_release.py") $Bundle
if ($LASTEXITCODE -ne 0) { throw "Portable release verification failed" }
Write-Host "Portable bundle ready: $Bundle"
