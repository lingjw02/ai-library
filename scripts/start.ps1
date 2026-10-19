param(
    [ValidateRange(1024, 65535)][int]$Port = 8000,
    [string]$DataDir = ''
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw 'Missing .venv. Run uv sync --locked from the project directory first.'
}
if ($DataDir) {
    $env:AI_LIBRARY_DATA_DIR = [System.IO.Path]::GetFullPath($DataDir)
}
Push-Location -LiteralPath $projectRoot
try {
    Write-Host "AI Library: http://127.0.0.1:$Port (Ctrl+C to stop)"
    & $pythonExe -m uvicorn backend.adapters.api.app:app --host 127.0.0.1 --port $Port
    if ($LASTEXITCODE -ne 0) { throw "Server exited with code $LASTEXITCODE" }
} finally {
    Pop-Location
}
