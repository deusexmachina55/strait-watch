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

function Stop-Hard {
    # Ask the service manager to stop, wait up to 30s, then kill nssm and the app if still pending
    Write-Host "Stop-Service $ServiceName -NoWait" -ForegroundColor Yellow
    Stop-Service $ServiceName -NoWait -ErrorAction SilentlyContinue
    for ($i = 0; $i -lt 30; $i++) {
        if ((Get-Service $ServiceName).Status -eq 'Stopped') { Write-Host "Stopped after ${i}s"; return }
        Start-Sleep 1
    }
    $pid_ = (Get-CimInstance Win32_Service -Filter "Name='$ServiceName'").ProcessId
    Write-Host "Still $((Get-Service $ServiceName).Status). Killing process tree of PID $pid_" -ForegroundColor Yellow
    if ($pid_) { taskkill /PID $pid_ /T /F | Out-Null }
    Start-Sleep 2
    Write-Host "State: $((Get-Service $ServiceName).Status)"
}

if ($Action -in 'stop', 'restart') { Stop-Hard }
if ($Action -in 'start', 'restart') {
    Write-Host "Start-Service $ServiceName" -ForegroundColor Yellow
    Start-Service $ServiceName
    Write-Host "State: $((Get-Service $ServiceName).Status)"
}
if ($Action -ne 'uninstall') { exit }

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
