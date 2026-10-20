# One command on a Windows laptop (PowerShell): installs uv if missing, syncs the
# project, starts the app with demo data and opens the browser. Records live in
# .\cap-data and survive restarts. `.\run.ps1 --reset` starts clean.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Host "Installing uv (the Python project tool)..."
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

if (-not (Test-Path .env)) { Copy-Item .env.example .env }

uv sync --quiet
uv run cap serve --demo --host 0.0.0.0 --open @args
