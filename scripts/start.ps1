$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonCommand = Join-Path $projectRoot ".venv\Scripts\python.exe"
$logDirectory = Join-Path $projectRoot "tmp"
$stdoutLog = Join-Path $logDirectory "backend.stdout.log"
$stderrLog = Join-Path $logDirectory "backend.stderr.log"

if (-not (Test-Path $pythonCommand)) {
    throw "Python environment is missing. Run .\scripts\setup.ps1 first."
}

New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null

$backendProcess = Start-Process `
    -FilePath $pythonCommand `
    -ArgumentList @("-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", "8000") `
    -WorkingDirectory $projectRoot `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -WindowStyle Hidden `
    -PassThru

Write-Host "Backend started. Logs: $stdoutLog and $stderrLog"
Write-Host "Open http://127.0.0.1:5173 after the frontend starts."

try {
    pnpm --dir (Join-Path $projectRoot "frontend") dev
}
finally {
    if (-not $backendProcess.HasExited) {
        Stop-Process -Id $backendProcess.Id
    }
}
