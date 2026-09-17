#Requires -Version 7
<#
.SYNOPSIS
    Make a database snapshot suitable for giving to someone else: history kept, personal rows removed.
.DESCRIPTION
    Removes settings (login, keys), backup and job history, briefs, outlook grades, tripwire and LLM call logs.
    Keeps items, analysis, translations, PLA counts, GDELT, MSA warnings and zones, prices, advisories, odds, AIS/ADS-B sightings.
    The recipient restores it on their Backup page after installing.
.EXAMPLE
    pwsh -NoProfile -ExecutionPolicy Bypass -File C:\claudespace\strait-watch\installer\Export-ForSharing.ps1
#>
param([string]$OutDir = (Join-Path (Split-Path $PSScriptRoot -Parent) 'dist'))
$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
$python = Join-Path $Root '.venv\Scripts\python.exe'
New-Item -ItemType Directory -Force $OutDir | Out-Null
$out = Join-Path $OutDir "strait-share-$(Get-Date -Format yyyyMMdd).db"
$script = @"
import sqlite3
src = sqlite3.connect(r'file:$Root\data\strait.db?mode=ro', uri=True)
dst = sqlite3.connect(r'$out')
src.backup(dst)
src.close()
for table in ('settings', 'backups', 'briefs', 'outlook_scores', 'tripwire_log', 'llm_calls', 'job_status', 'job_failures'):
    dst.execute(f'DELETE FROM {table}')
dst.commit()
dst.execute('VACUUM')
dst.close()
print(r'$out')
"@
if (Test-Path $out) { Remove-Item $out -Force }
& $python -c $script
$mb = [math]::Round((Get-Item $out).Length / 1MB, 1)
Write-Host "Shareable snapshot: $out ($mb MB). Contains no login, keys or alert history." -ForegroundColor Green
