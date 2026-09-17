#Requires -Version 7
#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Upgrade an installed Strait Watch from a release zip. Keeps data\, logs\ and .env.
.EXAMPLE
    pwsh -NoProfile -ExecutionPolicy Bypass -File C:\claudespace\strait-watch\installer\upgrade.ps1 -Zip C:\Downloads\strait-watch-1.1.0-abc1234.zip
#>
param([Parameter(Mandatory)][string]$Zip)
$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
$ServiceName = 'StraitWatch'
if (-not (Test-Path $Zip)) { throw "zip not found: $Zip" }

$stage = Join-Path ([IO.Path]::GetTempPath()) 'strait-watch-upgrade'
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
Expand-Archive $Zip $stage -Force
if (-not (Test-Path (Join-Path $stage 'VERSION.txt'))) { throw 'not a Strait Watch release zip (VERSION.txt missing)' }
Write-Host "Upgrading to $(Get-Content (Join-Path $stage 'VERSION.txt'))"
Write-Host "Current: $(if (Test-Path (Join-Path $Root 'VERSION.txt')) { Get-Content (Join-Path $Root 'VERSION.txt') } else { 'unknown' })"

# Safety snapshot of the live database before touching anything
$python = Join-Path $Root '.venv\Scripts\python.exe'
$snap = Join-Path $Root "data\backups\strait-$(Get-Date -Format yyyyMMdd-HHmmss)-pre-upgrade.db"
New-Item -ItemType Directory -Force (Join-Path $Root 'data\backups') | Out-Null
& $python -c "import sqlite3; c = sqlite3.connect(r'$Root\data\strait.db'); c.execute('VACUUM INTO ?', (r'$snap',)); print('snapshot', r'$snap')"

Write-Host "Stopping $ServiceName" -ForegroundColor Yellow
Stop-Service $ServiceName -Force -ErrorAction SilentlyContinue
for ($i = 0; $i -lt 30 -and (Get-Service $ServiceName).Status -ne 'Stopped'; $i++) { Start-Sleep 1 }

# Replace code and runtime; leave data, logs, .env and the token file untouched
foreach ($item in Get-ChildItem $stage -Force) {
    if ($item.Name -in @('data', 'logs', '.env')) { continue }
    $dest = Join-Path $Root $item.Name
    if ($item.PSIsContainer) {
        robocopy $item.FullName $dest /MIR /NFL /NDL /NJH /NJS /NP | Out-Null
    } else {
        Copy-Item $item.FullName $dest -Force
    }
}
Remove-Item $stage -Recurse -Force

# The service account must be able to read the new files (inherits from the repo ACL set by setup.ps1)
icacls $Root /reset /T /C /Q | Out-Null
icacls $Root /inheritance:r /grant:r '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-18:(OI)(CI)F' "${env:USERDOMAIN}\${env:USERNAME}:(OI)(CI)F" 'svc-straitwatch:(OI)(CI)RX' /Q | Out-Null
icacls (Join-Path $Root 'data') /grant 'svc-straitwatch:(OI)(CI)M' /Q | Out-Null
icacls (Join-Path $Root 'logs') /grant 'svc-straitwatch:(OI)(CI)M' /Q | Out-Null

Write-Host "Starting $ServiceName" -ForegroundColor Yellow
Start-Service $ServiceName
Start-Sleep 5
Write-Host "State: $((Get-Service $ServiceName).Status). Check the System page for the version and job health."
