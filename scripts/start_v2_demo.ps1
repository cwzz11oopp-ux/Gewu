param(
    [ValidateSet("A", "B")]
    [string]$Demo = "A",
    [switch]$RunLive
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonCommand = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $pythonCommand)) {
    throw "Python environment is missing. Run .\scripts\setup.ps1 first."
}

if ($Demo -eq "A") {
    Write-Host "Demo A: deterministic threshold snapshot (offline; no live Qwen required)."
}
else {
    Write-Host "Demo B: validated public micrograd snapshot (live dependencies are required only for a fresh rerun)."
    if ($RunLive) {
        & $pythonCommand -u (Join-Path $PSScriptRoot "run_v2_public_repo_hardening.py")
        if ($LASTEXITCODE -ne 0) {
            throw "Live public-repository hardening did not complete. Inspect the persisted diagnostic."
        }
    }
}

Write-Host "After the app opens, visit http://127.0.0.1:5173/ and select Demo $Demo."
& (Join-Path $PSScriptRoot "start.ps1")
