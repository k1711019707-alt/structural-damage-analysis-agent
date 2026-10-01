param([string]$PythonExe = "D:\anaconda\envs\YOLO11-HAI\python.exe", [string]$BundleRoot = "")
$ErrorActionPreference = "Stop"
$Root = if ($BundleRoot) { (Resolve-Path $BundleRoot).Path } else { (Resolve-Path (Join-Path $PSScriptRoot "..\")).Path }
$Checkpoint = Join-Path $Root "runs\p2_damage_reproduction\weights\last.pt"
$Data = Join-Path $Root "dataset\dataset.yaml"
if (-not (Test-Path -LiteralPath $Checkpoint)) { throw "P2 resume checkpoint not found: $Checkpoint" }
& $PythonExe -c "from ultralytics.cfg import entrypoint; entrypoint()" segment train model=$Checkpoint data=$Data resume=True
if ($LASTEXITCODE -ne 0) { throw "Resume training failed" }
