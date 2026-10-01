param([string]$PythonExe = "D:\anaconda\envs\YOLO11-HAI\python.exe", [string]$Output = "environment_report.txt")
$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $PythonExe)) { throw "Python interpreter not found: $PythonExe" }
& $PythonExe -c "import platform,sys; print(platform.platform()); print(sys.version)" | Set-Content -LiteralPath $Output -Encoding UTF8
& $PythonExe -m pip freeze | Add-Content -LiteralPath $Output -Encoding UTF8
Write-Host "Environment report written: $Output"
