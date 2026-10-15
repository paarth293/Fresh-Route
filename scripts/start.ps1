# Windows PowerShell Startup & Verification Script for FreshRoute: LastWindow
$ErrorActionPreference = "Stop"
$env:PYTHONPATH = "$PSScriptRoot\..\backend"
$python = Join-Path $PSScriptRoot '..\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Run the README setup steps to create .venv first.' }

Write-Host "==> Running FreshRoute: LastWindow Verification & Test Suite..."
& $python "$PSScriptRoot\..\backend\tests\run_all_tests.py"
if ($LASTEXITCODE -ne 0) { throw 'Backend verification failed. Server was not started.' }
Push-Location "$PSScriptRoot\..\frontend"
try {
    npm run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed. Server was not started.' }
} finally { Pop-Location }

Write-Host "==> Starting FastAPI Backend Server on http://127.0.0.1:8000 ..."
& $python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
