$ErrorActionPreference = "Stop"

# Edit these values for the HQ server. Prefer Windows/enterprise secret management
# over storing SECRET_KEY in this file for the final deployment.
$env:CENTRAL_MODE = "1"
$env:DATABASE_URL = "postgresql+psycopg://solar_audit:CHANGE_ME@127.0.0.1:5432/solar_audit"
$env:SECRET_KEY = "CHANGE_ME_TO_A_LONG_RANDOM_SECRET"
$env:WAITRESS_HOST = "127.0.0.1"
$env:WAITRESS_PORT = "8080"
$env:WAITRESS_THREADS = "8"

$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location (Join-Path $Root "backend")

# If using a venv, replace this with the full path to its python.exe.
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { $Python = "python" }

& $Python "production_server.py"
