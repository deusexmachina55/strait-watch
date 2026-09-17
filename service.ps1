#Requires -Version 7
<#
.SYNOPSIS
    Manage the Strait Watch Windows service. Install with setup.ps1.
.EXAMPLE
    ./service.ps1 status
    ./service.ps1 restart
#>
param(
    [Parameter(Mandatory)]
    [ValidateSet('status', 'start', 'stop', 'restart', 'uninstall')]
    [string]$Action
)
$ErrorActionPreference = 'Stop'

$ServiceName = 'StraitWatch'
$nssm = @('C:\Program Files\nssm\nssm.exe') + @(Get-ChildItem 'C:\Program Files\WinGet\Packages\NSSM.NSSM*' -Recurse -Filter nssm.exe -ErrorAction SilentlyContinue |
    Where-Object FullName -match '\\win64\\' | Select-Object -ExpandProperty FullName) |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $nssm) { throw 'NSSM not found. Run setup.ps1 first.' }

if ($Action -eq 'status') {
    & $nssm status $ServiceName
    Get-CimInstance Win32_Service -Filter "Name='$ServiceName'" | Select-Object State, StartMode, StartName, ProcessId
    exit
}

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { throw "'$Action' needs an elevated PowerShell." }

if ($Action -ne 'uninstall') {
    Write-Host "& '$nssm' $Action $ServiceName" -ForegroundColor Yellow
    & $nssm $Action $ServiceName
    exit
}

$commands = @"
& '$nssm' stop $ServiceName
& '$nssm' remove $ServiceName confirm
Get-NetFirewallRule -DisplayName 'Strait Watch' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
Remove-LocalUser -Name 'svc-straitwatch' -ErrorAction SilentlyContinue
"@
Write-Host $commands -ForegroundColor Yellow
if ((Read-Host 'Run this? [y/N]') -notmatch '^[yY]$') { throw 'Aborted' }
& ([scriptblock]::Create($commands))
Write-Host 'Uninstalled. Repo ACLs, data\ and logs\ are left in place.'
