$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv
}

$pythonCommand = Join-Path $projectRoot ".venv\Scripts\python.exe"
& $pythonCommand -m pip install -r requirements.txt

if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
    if (-not (Get-Command corepack -ErrorAction SilentlyContinue)) {
        throw "pnpm or Corepack is required. Install Node.js 20.19+ or 22.12+ first."
    }
    corepack enable
    corepack prepare pnpm@11.10.0 --activate
}

pnpm --dir frontend install --frozen-lockfile

if (-not (Test-Path ".env")) {
    Copy-Item ".env.demo.example" ".env"
    Write-Host "Created .env from the no-key demo configuration."
}

Write-Host "Setup complete. Run .\scripts\start.ps1"
