param([string]$Executable = "")

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$runtimeRoot = Join-Path $projectRoot "artifacts\qdrant"
if (-not $Executable) { $Executable = Join-Path $runtimeRoot "server\qdrant.exe" }
$Executable = [IO.Path]::GetFullPath($Executable)
$pidFile = Join-Path $runtimeRoot "qdrant.pid"
if (-not (Test-Path -LiteralPath $pidFile)) {
    Write-Output "No recorded Qdrant process"
    exit 0
}
$serverProcess = Get-Process -Id ([int](Get-Content -LiteralPath $pidFile)) -ErrorAction SilentlyContinue
if ($serverProcess) {
    if ($serverProcess.Path -ne $Executable) {
        throw "Recorded PID does not match the expected executable; refusing to stop another process"
    }
    Stop-Process -InputObject $serverProcess
    $serverProcess.WaitForExit()
}
Remove-Item -LiteralPath $pidFile
Write-Output "Qdrant stopped; persistent storage retained"
