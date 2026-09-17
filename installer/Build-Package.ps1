#Requires -Version 7
<#
.SYNOPSIS
    Rebuild this installer folder from the source PC: strait-watch.zip (program with runtime),
    a shareable database snapshot, and VERSION.txt. Copy the whole folder to install elsewhere.
.PARAMETER NoData
    Skip the database snapshot.
#>
param([switch]$NoData)
$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
$Out = $PSScriptRoot

$version = (Select-String -Path (Join-Path $Root 'app\version.py') -Pattern 'VERSION = "([^"]+)"').Matches[0].Groups[1].Value
$git = (& git -C $Root rev-parse --short HEAD 2>$null) ?? 'nogit'
$stage = Join-Path ([IO.Path]::GetTempPath()) 'strait-watch-build'
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Force $stage | Out-Null

# Program: code, docs, runtime. No data, logs, secrets, git or caches.
$include = @('app', '.python', '.venv', 'config.toml', 'pyproject.toml', 'uv.lock', '.python-version',
             'setup.ps1', 'service.ps1', 'README.md', 'SPEC.md', 'DECISIONS.md')
foreach ($item in $include) {
    $src = Join-Path $Root $item
    if (-not (Test-Path $src)) { Write-Warning "missing: $item"; continue }
    Write-Host "  + $item"
    if (Test-Path $src -PathType Container) {
        robocopy $src (Join-Path $stage $item) /E /XD __pycache__ .pytest_cache /XF *.pyc /NFL /NDL /NJH /NJS /NP | Out-Null
    } else {
        Copy-Item $src (Join-Path $stage $item)
    }
}
Get-ChildItem $stage -Recurse -Force -Include '.env', '*token*.txt', 'strait.db*', 'initial-password.txt' | Remove-Item -Force

$zip = Join-Path $Out 'strait-watch.zip'
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zip -CompressionLevel Optimal
Remove-Item $stage -Recurse -Force
Set-Content (Join-Path $Out 'VERSION.txt') "Strait Watch $version ($git), built $(Get-Date -Format 'yyyy-MM-dd HH:mm')"

# Shareable snapshot: history without settings, keys, logs or briefs
Get-ChildItem $Out -Filter 'strait-share-*.db' | Remove-Item -Force
if (-not $NoData -and (Test-Path (Join-Path $Root 'data\strait.db'))) {
    $db = Join-Path $Out "strait-share-$(Get-Date -Format yyyyMMdd).db"
    $python = Join-Path $Root '.venv\Scripts\python.exe'
    & $python -c @"
import sqlite3
src = sqlite3.connect(r'file:$Root\data\strait.db?mode=ro', uri=True)
dst = sqlite3.connect(r'$db')
src.backup(dst); src.close()
for t in ('settings', 'backups', 'briefs', 'outlook_scores', 'tripwire_log', 'llm_calls', 'job_status', 'job_failures'):
    dst.execute(f'DELETE FROM {t}')
dst.commit(); dst.execute('VACUUM'); dst.close()
"@
    Write-Host "  + $(Split-Path $db -Leaf) ($([math]::Round((Get-Item $db).Length / 1MB, 1)) MB, no settings, keys or logs)"
}

Write-Host "`nInstaller folder ready: $Out" -ForegroundColor Green
Get-ChildItem $Out | Where-Object Name -ne 'Build-Package.ps1' | ForEach-Object { "  {0,-28} {1,8:n1} MB" -f $_.Name, ($_.Length / 1MB) }
Write-Host "Copy the whole folder to the target PC and run INSTALL.cmd."
