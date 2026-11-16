param(
    [Parameter(Mandatory=$true)]
    [string]$BackendPath
)

$ErrorActionPreference = "Stop"
$BackendPath = (Resolve-Path $BackendPath).Path
$PatchRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

$SourceModels = Join-Path $PatchRoot "backend\models.py"
$SourceTest = Join-Path $PatchRoot "backend\tests\test_central_model_foundation.py"
$TargetModels = Join-Path $BackendPath "models.py"
$TargetTestsDir = Join-Path $BackendPath "tests"
$TargetTest = Join-Path $TargetTestsDir "test_central_model_foundation.py"

if (!(Test-Path $TargetModels)) {
    throw "models.py not found at: $TargetModels"
}
if (!(Test-Path $SourceModels) -or !(Test-Path $SourceTest)) {
    throw "Patch files are missing. Extract the complete patch ZIP before running this script."
}

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$BackupModels = "$TargetModels.pre_M1_$stamp.bak"
Copy-Item $TargetModels $BackupModels
New-Item -ItemType Directory -Force -Path $TargetTestsDir | Out-Null
Copy-Item $SourceModels $TargetModels -Force
Copy-Item $SourceTest $TargetTest -Force

Write-Host "M1 files applied."
Write-Host "Backup: $BackupModels"
Write-Host "Next: activate your backend virtual environment and run:"
Write-Host "python -m pytest tests/test_central_model_foundation.py -q"
Write-Host "Do NOT run database migrations or bootstrap against a live database."
