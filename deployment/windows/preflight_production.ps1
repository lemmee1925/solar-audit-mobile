$ErrorActionPreference = 'Stop'
Write-Host 'Solar Audit production preflight' -ForegroundColor Cyan

$required = @('CENTRAL_MODE','DATABASE_URL','SECRET_KEY')
foreach ($name in $required) {
    if (-not (Get-Item "Env:$name" -ErrorAction SilentlyContinue)) {
        throw "$name is not set."
    }
}
if ($env:CENTRAL_MODE -ne '1') { throw 'CENTRAL_MODE must be 1.' }
if ($env:SECRET_KEY.Length -lt 32) { throw 'SECRET_KEY must be at least 32 characters.' }
if ($env:DATABASE_URL -match 'CHANGE_ME|solar_audit_test|solar_audit_staging') {
    throw 'DATABASE_URL still points to a test/staging/placeholder database.'
}

$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { throw "Python venv not found: $Python" }

Push-Location (Join-Path $Root 'backend')
try {
    & $Python -c "from app import app; print(app.config.get('ENV','production')); print('APP_IMPORT_OK')"
    if ($LASTEXITCODE -ne 0) { throw 'Application import failed.' }
    & $Python -c "from models import engine; print(engine.url.get_backend_name()); print('DB_DIALECT_OK')"
    if ($LASTEXITCODE -ne 0) { throw 'Database configuration/import failed.' }
} finally { Pop-Location }
Write-Host 'PRE-FLIGHT PASSED' -ForegroundColor Green
