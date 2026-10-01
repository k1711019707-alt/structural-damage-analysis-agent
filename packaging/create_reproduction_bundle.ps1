param(
    [string]$Destination = "",
    [string]$PythonExe = "D:\anaconda\envs\YOLO11-HAI\python.exe"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\")).Path
$WorkspaceRoot = (Resolve-Path (Join-Path $ProjectRoot "..\")).Path
$DatasetSource = Join-Path $WorkspaceRoot "dataset_yolo11_seg"
$Destination = if ($Destination) { [IO.Path]::GetFullPath($Destination) } else { Join-Path $WorkspaceRoot "artifacts\YOLO11DamageDesktop-Reproduction-1.0.0" }

if (-not (Test-Path -LiteralPath $DatasetSource)) { throw "Dataset not found: $DatasetSource" }
New-Item -ItemType Directory -Force -Path $Destination | Out-Null

function Copy-Tree([string]$Source, [string]$Target) {
    New-Item -ItemType Directory -Force -Path $Target | Out-Null
    & robocopy $Source $Target /E /R:1 /W:1 /NFL /NDL /NP /NJH /NJS /XF *.npy *.cache *.pyc /XD __pycache__ .pytest_cache
    if ($LASTEXITCODE -gt 7) { throw "robocopy failed ($LASTEXITCODE): $Source" }
}

Copy-Tree (Join-Path $DatasetSource "images") (Join-Path $Destination "dataset\images")
Copy-Tree (Join-Path $DatasetSource "labels") (Join-Path $Destination "dataset\labels")
Copy-Item -LiteralPath (Join-Path $DatasetSource "manifest.csv") -Destination (Join-Path $Destination "dataset\manifest.csv") -Force
Copy-Item -LiteralPath (Join-Path $DatasetSource "audit_report.json") -Destination (Join-Path $Destination "dataset\audit_report.json") -Force

$datasetYaml = @"
path: ./dataset
train: images/train
val: images/val
test: images/test
names:
  0: Concrete crushing
  1: Delamination
  2: Microcrack
  3: Minor spalling
  4: Moderate spalling
  5: Rebar corrosion
  6: Structural crack
  7: Structural deformation
"@
Set-Content -LiteralPath (Join-Path $Destination "dataset\dataset.yaml") -Value $datasetYaml.TrimStart() -Encoding UTF8

Copy-Tree (Join-Path $ProjectRoot "configs") (Join-Path $Destination "configs")
Copy-Item -LiteralPath (Join-Path $ProjectRoot "environment.yml") -Destination (Join-Path $Destination "environment.yml") -Force
Copy-Item -LiteralPath (Join-Path $ProjectRoot "requirements-lock.txt") -Destination (Join-Path $Destination "requirements-lock.txt") -Force
New-Item -ItemType Directory -Force -Path (Join-Path $Destination "models") | Out-Null
Copy-Item -LiteralPath (Join-Path $ProjectRoot "deployment\models\model_manifest.json") -Destination (Join-Path $Destination "models\model_manifest.json") -Force
Copy-Item -LiteralPath (Join-Path $ProjectRoot "models\best.pt") -Destination (Join-Path $Destination "models\best.pt") -Force
Copy-Tree (Join-Path $ProjectRoot "scripts") (Join-Path $Destination "scripts")

@"
# YOLO11 Damage Desktop reproduction bundle

This bundle contains the formal train/val/test images and labels, portable dataset.yaml,
the formal P2 checkpoint, portable P2 architecture/training configuration, and the locked
environment. It intentionally excludes .npy/.cache files, legacy checkpoints and runs, personal knowledge-base data,
Word report templates, and user run outputs.

## Reproduce

1. Create the YOLO11-HAI environment from environment.yml (or install requirements-lock.txt).
2. Run scripts/train_damage_reproduction.ps1 for controlled P2 continued training; after it creates a last checkpoint, scripts/resume_damage_reproduction.ps1 can resume it.
3. Run scripts/evaluate_damage_reproduction.ps1 against models/best.pt.

Different GPUs may produce different checkpoint hashes; compare the recorded metrics within
the tolerances in reproduction_manifest.json instead of requiring byte-identical weights.
"@ | Set-Content -LiteralPath (Join-Path $Destination "README.md") -Encoding UTF8

$manifest = [ordered]@{
    product = "YOLO11DamageDesktop"
    bundle = "training-reproduction"
    version = "2.0.0"
    created_at_utc = [DateTime]::UtcNow.ToString("o")
    training_lineage = "Project-contained P2-YOLO11s best.pt initialization; formal dataset train/val/test split; no legacy checkpoint or reference run"
    metric_tolerances = @{ map50 = 0.03; map50_95 = 0.03; precision = 0.03; recall = 0.03 }
    reproducibility_note = "Different GPUs and library kernels may produce non-byte-identical checkpoints; compare fixed-test metrics within tolerances."
    word_report_templates = @()
    files = @()
}
Get-ChildItem -LiteralPath $Destination -File -Recurse | Where-Object { $_.Name -ne "reproduction_manifest.json" } | ForEach-Object {
    $relative = $_.FullName.Substring($Destination.Length + 1).Replace("\", "/")
    $manifest.files += [ordered]@{ path = $relative; size_bytes = $_.Length; sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
}
$manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $Destination "reproduction_manifest.json") -Encoding UTF8
Write-Host "Reproduction bundle ready: $Destination"
