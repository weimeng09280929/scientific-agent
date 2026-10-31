# Separate local accounts/workspace. Registration does not verify email yet.
$sraRepo = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $sraRepo
try {
    & (Join-Path $sraRepo 'venv\Scripts\python.exe') -m tools.serve_local --mode accounts --port 8077 --allow-registration
} finally {
    Pop-Location
}
