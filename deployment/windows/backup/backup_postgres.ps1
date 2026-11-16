param(
    [string]$BackupDir = 'D:\SolarAuditBackups\postgres'
)
$ErrorActionPreference = 'Stop'
if (-not $env:PGHOST) { $env:PGHOST='127.0.0.1' }
if (-not $env:PGPORT) { $env:PGPORT='5432' }
if (-not $env:PGDATABASE) { $env:PGDATABASE='solar_audit_prod' }
if (-not $env:PGUSER) { $env:PGUSER='solar_audit_prod' }
New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$out = Join-Path $BackupDir "solar_audit_prod-$stamp.dump"
pg_dump --format=custom --file="$out" $env:PGDATABASE
if ($LASTEXITCODE -ne 0) { throw 'pg_dump failed.' }
Write-Host "Backup created: $out" -ForegroundColor Green
