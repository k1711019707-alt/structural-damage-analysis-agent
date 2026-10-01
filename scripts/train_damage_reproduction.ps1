param([string]$PythonExe = "D:\anaconda\envs\YOLO11-HAI\python.exe", [string]$BundleRoot = "")
$ErrorActionPreference = "Stop"
$Root = if ($BundleRoot) { (Resolve-Path $BundleRoot).Path } else { (Resolve-Path (Join-Path $PSScriptRoot "..\")).Path }
$Data = Join-Path $Root "dataset\dataset.yaml"
$Model = Join-Path $Root "models\best.pt"
$Project = Join-Path $Root "runs"
& $PythonExe -c "from ultralytics.cfg import entrypoint; entrypoint()" segment train model=$Model data=$Data project=$Project name=p2_damage_reproduction imgsz=960 epochs=100 batch=4 device=0 workers=0 mask_ratio=2 overlap_mask=True close_mosaic=10 cos_lr=True
if ($LASTEXITCODE -ne 0) { throw "Training failed" }
