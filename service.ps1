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
$nssm = 'C:\Program Files\nssm\nssm.exe'
if (-not (Test-Path $nssm)) { throw 'NSSM not found. Run setup.ps1 first.' }

if ($Action -eq 'status') {
    & $nssm status $ServiceName
    Get-CimInstance Win32_Service -Filter "Name='$ServiceName'" | Select-Object State, StartMode, StartName, ProcessId
    exit
}

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { throw "'$Action' needs an elevated PowerShell." }

# Use the Windows service manager for start/stop; nssm's own commands can hang waiting for the app
if ($Action -ne 'uninstall') {
    $cmd = "{0}-Service $ServiceName" -f (Get-Culture).TextInfo.ToTitleCase($Action)
    Write-Host $cmd -ForegroundColor Yellow
    & ([scriptblock]::Create($cmd))
    Get-Service $ServiceName | Select-Object Status
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
