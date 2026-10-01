param([string]$PythonExe = "D:\anaconda\envs\YOLO11-HAI\python.exe", [string]$BundleRoot = "", [string]$Weights = "")
$ErrorActionPreference = "Stop"
$Root = if ($BundleRoot) { (Resolve-Path $BundleRoot).Path } else { (Resolve-Path (Join-Path $PSScriptRoot "..\")).Path }
$Checkpoint = if ($Weights) { $Weights } else { Join-Path $Root "models\best.pt" }
$Data = Join-Path $Root "dataset\dataset.yaml"
$Output = Join-Path $Root "evaluation"
& $PythonExe -c "from ultralytics.cfg import entrypoint; entrypoint()" segment val model=$Checkpoint data=$Data split=test project=$Output name=damage_reproduction_test
if ($LASTEXITCODE -ne 0) { throw "Evaluation failed" }
