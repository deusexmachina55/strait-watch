#Requires -Version 7
#Requires -RunAsAdministrator
<#
.SYNOPSIS
    One-shot, idempotent installer for Strait Watch.
.DESCRIPTION
    Installs uv, a repo-local Python and dependencies, creates .env, applies the
    security baseline from DECISIONS.md, installs the Windows service and sends a
    Telegram test alert. Every system-level change is printed and needs confirmation.
#>
param(
    [string]$InterfaceAlias = 'Ethernet',
    [string]$LanSubnet = '192.168.0.0/24',
    [int]$Port = 8080
)
$ErrorActionPreference = 'Stop'

$Root = $PSScriptRoot
$ServiceName = 'StraitWatch'
$ServiceUser = 'svc-straitwatch'
$EnvFile = Join-Path $Root '.env'
$NssmDir = 'C:\Program Files\nssm'
$NssmZipUrl = 'https://nssm.cc/ci/nssm-2.24-101-g897c7ad.zip'
# InstallerSha256 from the NSSM.NSSM winget manifest for the same URL
$NssmZipSha256 = '99F5045FFFBFFB745D67FE3A065A953C4A3D9C253B868892D9B685B0EE7D07B8'

function Invoke-Step([string]$Title, [string]$Command) {
    Write-Host "`n== $Title ==" -ForegroundColor Cyan
    Write-Host $Command -ForegroundColor Yellow
    if ((Read-Host 'Run this? [y/N]') -notmatch '^[yY]$') { throw "Aborted at: $Title" }
    & ([scriptblock]::Create($Command))
}

function Get-EnvValue([string]$Key) {
    $line = Get-Content $EnvFile | Where-Object { $_ -match "^$Key=" } | Select-Object -First 1
    if ($line) { ($line -replace "^$Key=", '').Trim("'") } else { '' }
}

function Set-EnvValue([string]$Key, [string]$Value) {
    if ($Value -match "'") { throw "$Key must not contain a single quote" }
    $lines = @(Get-Content $EnvFile | Where-Object { $_ -notmatch "^$Key=" }) + "$Key='$Value'"
    Set-Content $EnvFile $lines
}

function Read-Secret([string]$Prompt) {
    do { $value = Read-Host $Prompt -MaskInput } while (-not $value)
    $value
}

function Get-WingetNssm {
    Get-ChildItem 'C:\Program Files\WinGet\Packages\NSSM.NSSM*' -Recurse -Filter nssm.exe -ErrorAction SilentlyContinue |
        Where-Object FullName -match '\\win64\\' | Select-Object -First 1 -ExpandProperty FullName
}

function Test-Service { [bool](Get-Service $ServiceName -ErrorAction SilentlyContinue) }

# 1. uv, repo-local Python, dependencies
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Invoke-Step 'Install uv (winget)' 'winget install --id astral-sh.uv -e --disable-interactivity'
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
}
$env:UV_PYTHON_INSTALL_DIR = Join-Path $Root '.python'
Push-Location $Root
uv python install 3.12 --no-bin
uv sync --frozen
if ($LASTEXITCODE) { throw 'uv sync failed' }
Pop-Location

# 2. Folders and .env
New-Item -ItemType Directory -Force (Join-Path $Root 'data'), (Join-Path $Root 'logs') | Out-Null
if (-not (Test-Path $EnvFile)) { Copy-Item (Join-Path $Root '.env.example') $EnvFile }

if (-not (Get-EnvValue 'TELEGRAM_BOT_TOKEN')) {
    Set-EnvValue 'TELEGRAM_BOT_TOKEN' (Read-Secret 'Telegram bot token')
}
if (-not (Get-EnvValue 'BASIC_AUTH_USER')) {
    Set-EnvValue 'BASIC_AUTH_USER' (Read-Host 'Dashboard username')
}
if (-not (Get-EnvValue 'BASIC_AUTH_PASS')) {
    do {
        $pass = Read-Secret 'Dashboard password (min 16 chars)'
        $confirm = Read-Secret 'Confirm password'
    } while ($pass.Length -lt 16 -or $pass -cne $confirm)
    Set-EnvValue 'BASIC_AUTH_PASS' $pass
}
if (-not (Get-EnvValue 'TELEGRAM_CHAT_ID')) {
    $token = Get-EnvValue 'TELEGRAM_BOT_TOKEN'
    Read-Host 'Send any message to your bot in Telegram, then press Enter'
    $updates = Invoke-RestMethod "https://api.telegram.org/bot$token/getUpdates"
    $chat = ($updates.result | Where-Object { $_.message } | Select-Object -Last 1).message.chat
    if (-not $chat) { throw 'No message found in getUpdates. Message the bot and run setup again.' }
    if ((Read-Host "Use chat $($chat.id) ($($chat.username)$($chat.title))? [y/N]") -notmatch '^[yY]$') { throw 'Chat ID not confirmed' }
    Set-EnvValue 'TELEGRAM_CHAT_ID' $chat.id
}

# 3. Network profile: the firewall rule only applies on a Private network
if ((Get-NetConnectionProfile -InterfaceAlias $InterfaceAlias).NetworkCategory -ne 'Private') {
    Invoke-Step 'Set network profile to Private' "Set-NetConnectionProfile -InterfaceAlias '$InterfaceAlias' -NetworkCategory Private"
}

# 4. NSSM in Program Files. winget keeps the installing user's ACL on the extracted exe,
#    which the service account cannot execute. A copy inherits the Program Files ACL.
$nssm = Join-Path $NssmDir 'nssm.exe'
if (-not (Test-Path $nssm)) {
    if (-not (Get-WingetNssm)) {
        Invoke-Step 'Install NSSM (winget, machine scope)' 'winget install --id NSSM.NSSM -e --scope machine --disable-interactivity'
    }
    $wingetNssm = Get-WingetNssm
    if ($wingetNssm) {
        Invoke-Step 'Copy NSSM to Program Files' @"
New-Item -ItemType Directory -Force '$NssmDir' | Out-Null
Copy-Item '$wingetNssm' '$nssm'
"@
    } else {
        Invoke-Step 'Install NSSM from official zip (hash verified)' @"
`$zip = Join-Path `$env:TEMP 'nssm.zip'
`$tmp = Join-Path `$env:TEMP 'nssm'
Invoke-WebRequest '$NssmZipUrl' -OutFile `$zip
if ((Get-FileHash `$zip -Algorithm SHA256).Hash -ne '$NssmZipSha256') { throw 'NSSM hash mismatch' }
Expand-Archive `$zip `$tmp -Force
New-Item -ItemType Directory -Force '$NssmDir' | Out-Null
Copy-Item (Join-Path `$tmp 'nssm-2.24-101-g897c7ad\win64\nssm.exe') '$NssmDir\nssm.exe'
"@
    }
}
if (-not (Test-Path $nssm)) { throw 'NSSM not found after install' }

# 5. Service account (password is random, never stored or shown)
if (-not (Test-Service)) {
    $bytes = [byte[]]::new(24)
    [Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    $pwPlain = [Convert]::ToBase64String($bytes) + 'aA1!'
    $pw = ConvertTo-SecureString $pwPlain -AsPlainText -Force
    if (Get-LocalUser $ServiceUser -ErrorAction SilentlyContinue) {
        Invoke-Step 'Reset service account password' "Set-LocalUser -Name '$ServiceUser' -Password `$pw"
    } else {
        Invoke-Step 'Create service account (standard user)' "New-LocalUser -Name '$ServiceUser' -Password `$pw -PasswordNeverExpires -UserMayNotChangePassword -Description 'Strait Watch service'"
    }
}

# 6. ACLs: service gets Read & Execute on the repo, Modify on data\ and logs\ only, Read on .env
$me = "$env:USERDOMAIN\$env:USERNAME"
Invoke-Step 'Apply repo ACLs' @"
icacls '$Root' /reset /T /C /Q
icacls '$Root' /inheritance:r /grant:r '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-18:(OI)(CI)F' '${me}:(OI)(CI)F' '${ServiceUser}:(OI)(CI)RX' /Q
icacls '$Root\data' /grant '${ServiceUser}:(OI)(CI)M' /Q
icacls '$Root\logs' /grant '${ServiceUser}:(OI)(CI)M' /Q
icacls '$EnvFile' /inheritance:r /grant:r '*S-1-5-32-544:F' '*S-1-5-18:F' '${me}:F' '${ServiceUser}:R' /Q
"@

# 7. Firewall: inbound TCP port, Private profile, LAN subnet only
$rule = Get-NetFirewallRule -DisplayName 'Strait Watch' -ErrorAction SilentlyContinue
$ruleOk = $rule -and $rule.Profile -eq 'Private' -and
    ($rule | Get-NetFirewallAddressFilter).RemoteAddress -contains $LanSubnet -and
    ($rule | Get-NetFirewallPortFilter).LocalPort -contains "$Port"
if (-not $ruleOk) {
    Invoke-Step 'Create firewall rule' @"
Get-NetFirewallRule -DisplayName 'Strait Watch' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName 'Strait Watch' -Direction Inbound -Protocol TCP -LocalPort $Port -Profile Private -RemoteAddress $LanSubnet -Action Allow | Out-Null
"@
}

# 8. Service
$python = Join-Path $Root '.venv\Scripts\python.exe'
$log = Join-Path $Root 'logs\service.log'
if (-not (Test-Service)) {
    Invoke-Step 'Install and start service' @"
& '$nssm' install $ServiceName '$python' -m uvicorn app.main:app --host 0.0.0.0 --port $Port --no-access-log
& '$nssm' set $ServiceName AppDirectory '$Root'
& '$nssm' set $ServiceName AppEnvironmentExtra PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
& '$nssm' set $ServiceName ObjectName '.\$ServiceUser' `$pwPlain
& '$nssm' set $ServiceName Start SERVICE_DELAYED_AUTO_START
& '$nssm' set $ServiceName AppExit Default Restart
& '$nssm' set $ServiceName AppRestartDelay 10000
& '$nssm' set $ServiceName AppStdout '$log'
& '$nssm' set $ServiceName AppStderr '$log'
& '$nssm' set $ServiceName AppRotateFiles 1
& '$nssm' set $ServiceName AppRotateOnline 1
& '$nssm' set $ServiceName AppRotateBytes 10485760
& '$nssm' start $ServiceName
"@
} else {
    if ((Get-CimInstance Win32_Service -Filter "Name='$ServiceName'").PathName.Trim('"') -ne $nssm) {
        Invoke-Step 'Point service at NSSM in Program Files' "sc.exe config $ServiceName binPath= '`"$nssm`"'"
    }
    Invoke-Step 'Restart service' "Restart-Service $ServiceName"
}
Remove-Variable pwPlain, pw -ErrorAction SilentlyContinue

# 9. Health check and Telegram test alert
$cred = [pscredential]::new((Get-EnvValue 'BASIC_AUTH_USER'), (ConvertTo-SecureString (Get-EnvValue 'BASIC_AUTH_PASS') -AsPlainText -Force))
$auth = @{ Authentication = 'Basic'; Credential = $cred; AllowUnencryptedAuthentication = $true }
$health = $null
for ($i = 0; $i -lt 60 -and $health.status -ne 'ok'; $i++) {
    Start-Sleep 2
    $health = try { Invoke-RestMethod "http://localhost:$Port/health" @auth } catch { $null }
}
if ($health.status -ne 'ok') { throw "Service not healthy. Check $log" }
Write-Host "`nService healthy: $($health.time)" -ForegroundColor Green

Invoke-RestMethod "http://localhost:$Port/api/test-alert" -Method Post -Headers @{ 'X-Requested-With' = 'strait-watch' } @auth | Out-Null
Write-Host 'Telegram test alert sent.' -ForegroundColor Green
Write-Host "Dashboard: http://$((Get-NetIPAddress -InterfaceAlias $InterfaceAlias -AddressFamily IPv4).IPAddress):$Port/"
