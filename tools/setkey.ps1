# Asks for the Anthropic API key (typed masked) and writes it to the local .env only.
# Called by install.bat on first run, and by set-key.bat whenever you want to change it.
param([switch]$Force)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
if (-not (Test-Path .env)) { Copy-Item .env.example .env }

$lines = Get-Content .env
$has = $lines | Where-Object { $_ -match '^CAP_ANTHROPIC_API_KEY=sk-' }
if ($has -and -not $Force) {
  Write-Host "  API key: already in .env (model backend anthropic). Run set-key.bat to change it."
  exit 0
}

Write-Host ""
Write-Host "  Anthropic API key. Typed masked; saved only to $(Get-Location)\.env; never sent anywhere but Anthropic."
Write-Host "  Leave blank for mock mode (no real drafts)."
$secure = Read-Host "  API key" -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try { $key = [Runtime.InteropServices.Marshal]::PtrToStringAuto($ptr) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
$key = ($key | ForEach-Object { $_.Trim() })

$kept = $lines | Where-Object { $_ -notmatch '^#?\s*CAP_ANTHROPIC_API_KEY=' -and $_ -notmatch '^CAP_MODEL_BACKEND=' }
if ($key) {
  if ($key -notlike 'sk-*') { Write-Host "  That does not look like an Anthropic key (they start with sk-). Saved anyway; check it if drafts park."; }
  $kept += 'CAP_MODEL_BACKEND=anthropic'
  $kept += ('CAP_ANTHROPIC_API_KEY=' + $key)
  Set-Content -Path .env -Value $kept -Encoding ASCII
  Write-Host "  Saved. Drafts and judges will run on the real models."
} else {
  $kept += 'CAP_MODEL_BACKEND=mock'
  Set-Content -Path .env -Value $kept -Encoding ASCII
  Write-Host "  No key: mock mode. Run set-key.bat any time to add one."
}
$key = $null
