param([string]$Executable = "")

$ErrorActionPreference = "Stop"
if ($env:OS -ne "Windows_NT") {
    throw "Use Docker Compose on Linux/macOS. This launcher installs the official Windows server."
}
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$runtimeRoot = Join-Path $projectRoot "artifacts\qdrant"
New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
if (-not $Executable) {
    $Executable = Join-Path $runtimeRoot "server\qdrant.exe"
    if (-not (Test-Path -LiteralPath $Executable)) {
        $archive = Join-Path $runtimeRoot "qdrant-v1.19.2.zip"
        $url = "https://github.com/qdrant/qdrant/releases/download/v1.19.2/qdrant-x86_64-pc-windows-msvc.zip"
        Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $archive
        $expectedHash = "7d86596f16c6e85d45a50312f5e16ccb51e059b62e3d7308110cb65b4c799a4d"
        if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) {
            throw "Qdrant release SHA256 mismatch"
        }
        Expand-Archive -LiteralPath $archive -DestinationPath (Join-Path $runtimeRoot "server") -Force
    }
}
$Executable = (Resolve-Path -LiteralPath $Executable).Path
$pidFile = Join-Path $runtimeRoot "qdrant.pid"
if (Test-Path -LiteralPath $pidFile) {
    $serverProcess = Get-Process -Id ([int](Get-Content -LiteralPath $pidFile)) -ErrorAction SilentlyContinue
    if ($serverProcess -and $serverProcess.Path -eq $Executable) {
        $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:6333/readyz" -TimeoutSec 5
        if ($response.StatusCode -ne 200) { throw "Recorded Qdrant process is not ready" }
        Write-Output "Qdrant already running: PID $($serverProcess.Id), http://127.0.0.1:6333"
        exit 0
    }
}
$configPath = Join-Path $PSScriptRoot "config\config.yaml"
$arguments = @("--config-path", ('"{0}"' -f $configPath), "--disable-telemetry")
$serverProcess = Start-Process -FilePath $Executable -ArgumentList $arguments -WorkingDirectory $PSScriptRoot `
    -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimeRoot "stdout.log") `
    -RedirectStandardError (Join-Path $runtimeRoot "stderr.log")
Set-Content -LiteralPath $pidFile -Value $serverProcess.Id
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:6333/readyz" -TimeoutSec 1
        if ($response.StatusCode -eq 200) {
            Write-Output "Qdrant ready: PID $($serverProcess.Id), http://127.0.0.1:6333"
            exit 0
        }
    } catch {
        if ($serverProcess.HasExited) { throw "Qdrant failed; see artifacts/qdrant/stderr.log" }
        Start-Sleep -Milliseconds 500
    }
}
throw "Qdrant readiness timeout; see artifacts/qdrant/stdout.log"
