param(
    [string]$PythonExe = "",
    [Parameter(Mandatory = $true)][string]$PortableStage,
    [string]$Destination = "",
    [string]$Version = "2.0.0",
    [switch]$ConfirmRagRebuildComplete
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\")).Path
$ProjectPython = Join-Path $ProjectRoot "YOLO11s\Scripts\python.exe"
if (-not $PythonExe) {
    if (Test-Path -LiteralPath $ProjectPython) { $PythonExe = $ProjectPython }
    else {
        $PythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($null -ne $PythonCommand) { $PythonExe = $PythonCommand.Source }
    }
}
if (-not (Test-Path -LiteralPath $PythonExe)) { throw "Python interpreter not found. Pass -PythonExe." }
if (-not $ConfirmRagRebuildComplete) { throw "Source release is blocked until the production-RAG rebuild is complete." }
if ($Version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$') { throw "Version must be SemVer-compatible: $Version" }
$StagePath = [IO.Path]::GetFullPath($PortableStage)
$Target = if ($Destination) { [IO.Path]::GetFullPath($Destination) } else { Join-Path $ProjectRoot "dist\source-$Version\YOLO11DamageDesktop-Source-$Version" }
if (Test-Path -LiteralPath $Target) { throw "Source release destination already exists: $Target" }
& $PythonExe (Join-Path $ProjectRoot "tools\source_release.py") `
    --project-root $ProjectRoot `
    --portable-stage $StagePath `
    --destination $Target `
    --version $Version
if ($LASTEXITCODE -ne 0) { throw "Source release build failed" }
Write-Host "Source release ready: $Target"
