<#
.SYNOPSIS
    Automated PostgreSQL + Photos backup for Solar Audit Central Mode.
.DESCRIPTION
    Creates a compressed pg_dump (.dump) and zips the photos/ directory into
    backups\YYYYMMDD_HHMMSS\ and prunes backups older than 14 days.
#>
$ErrorActionPreference = 'Stop'

$RootDir = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $RootDir ".env"
$BackupBase = Join-Path $RootDir "backups"
$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$TargetDir = Join-Path $BackupBase $Timestamp
New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null

# Load DATABASE_URL from .env if not already set in environment
if (-not $env:DATABASE_URL -and (Test-Path $EnvFile)) {
    foreach ($line in Get-Content $EnvFile) {
        if ($line -match '^DATABASE_URL=(.+)$') {
            $env:DATABASE_URL = $Matches[1].Trim()
        }
    }
}

if (-not $env:DATABASE_URL) {
    throw "DATABASE_URL is not set and was not found in .env"
}

$PgDump = "C:\Program Files\PostgreSQL\18\bin\pg_dump.exe"
if (-not (Test-Path $PgDump)) {
    $PgDump = "pg_dump"
}

# Convert SQLAlchemy URL (postgresql+psycopg://...) to standard libpq URI (postgresql://...)
$LibpqUrl = $env:DATABASE_URL -replace '^postgresql\+psycopg://', 'postgresql://'
$DumpFile = Join-Path $TargetDir "solar_audit_prod.dump"

Write-Host "Backing up PostgreSQL database to $DumpFile ..."
& $PgDump --format=custom --no-owner --no-acl --file=$DumpFile $LibpqUrl
if ($LASTEXITCODE -ne 0) {
    throw "pg_dump failed with exit code $LASTEXITCODE"
}

$PhotosDir = Join-Path $RootDir "photos"
if (Test-Path $PhotosDir) {
    $PhotosZip = Join-Path $TargetDir "photos.zip"
    Write-Host "Archiving photos/ to $PhotosZip ..."
    Compress-Archive -Path "$PhotosDir\*" -DestinationPath $PhotosZip -CompressionLevel Optimal -Force
}

# Prune backups older than 14 days
$Cutoff = (Get-Date).AddDays(-14)
Get-ChildItem -Path $BackupBase -Directory | Where-Object { $_.CreationTime -lt $Cutoff } | ForEach-Object {
    Write-Host "Pruning old backup: $($_.FullName)"
    Remove-Item -Path $_.FullName -Recurse -Force
}

Write-Host "Backup complete: $TargetDir"
