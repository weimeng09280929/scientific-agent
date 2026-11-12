# Local historical-project demo. Run from PowerShell; Ctrl+C stops this instance.
$sraRepo = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $sraRepo
try {
    & (Join-Path $sraRepo 'venv\Scripts\python.exe') -m tools.serve_local --mode demo --port 8001 --allow-live-model --allow-live-sources
} finally {
    Pop-Location
}
