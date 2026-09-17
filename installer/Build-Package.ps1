#Requires -Version 7
<#
.SYNOPSIS
    Build a distributable zip of Strait Watch: code, docs, installer scripts and the vendored Python runtime.
.DESCRIPTION
    Run on the development PC. Output: dist\strait-watch-<version>-<git>.zip
    Excluded: .git, data\, logs\, .env, token files, .claude, dist\, caches.
    The .venv is bound to C:\claudespace\strait-watch, so the target must unzip to the same path (install.cmd checks).
#>
param([string]$OutDir = (Join-Path (Split-Path $PSScriptRoot -Parent) 'dist'))
$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent

$version = (Select-String -Path (Join-Path $Root 'app\version.py') -Pattern 'VERSION = "([^"]+)"').Matches[0].Groups[1].Value
$git = (& git -C $Root rev-parse --short HEAD 2>$null) ?? 'nogit'
$name = "strait-watch-$version-$git"
$stage = Join-Path ([IO.Path]::GetTempPath()) $name
$zip = Join-Path $OutDir "$name.zip"

if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Force $stage, $OutDir | Out-Null

# Files and folders that make up a release
$include = @('app', 'installer', '.python', '.venv', 'config.toml', 'pyproject.toml', 'uv.lock', '.python-version',
             'setup.ps1', 'service.ps1', 'README.md', 'SPEC.md', 'DECISIONS.md', '.env.example')
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
# Never ship secrets, data or the token file, even if they somehow sit inside an included folder
Get-ChildItem $stage -Recurse -Force -Include '.env', '*token*.txt', 'strait.db*', 'initial-password.txt' | Remove-Item -Force

# Version stamp for the System page and upgrade checks
Set-Content (Join-Path $stage 'VERSION.txt') "$version $git $(Get-Date -Format s)"

if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zip -CompressionLevel Optimal
Remove-Item $stage -Recurse -Force
$mb = [math]::Round((Get-Item $zip).Length / 1MB, 1)
Write-Host "`nBuilt $zip ($mb MB)" -ForegroundColor Green
Write-Host "Target PC: unzip to C:\claudespace\strait-watch, then run installer\install.cmd as administrator."
