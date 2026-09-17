@echo off
setlocal EnableDelayedExpansion
rem ============================================================================
rem  Strait Watch one-pass installer. Double-click; it asks for administrator rights.
rem  Installs or upgrades to C:\claudespace\strait-watch from strait-watch.zip in this folder.
rem  Keeps existing data on upgrade. Afterwards: Settings page for keys, Backup page to restore.
rem ============================================================================
set "HERE=%~dp0"
set "TARGET=C:\claudespace\strait-watch"

net session >nul 2>&1
if errorlevel 1 (
  echo Requesting administrator rights...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

if not exist "%HERE%strait-watch.zip" (
  echo strait-watch.zip is missing from this folder. Build it with Build-Package.ps1 on the source PC.
  pause & exit /b 1
)

echo.
echo === Strait Watch installer ===
if exist "%HERE%VERSION.txt" type "%HERE%VERSION.txt"
echo Target: %TARGET%
echo.

rem 1. Stop the service if this is an upgrade
sc query StraitWatch >nul 2>&1
if not errorlevel 1 (
  echo Stopping existing service...
  sc stop StraitWatch >nul 2>&1
  timeout /t 10 /nobreak >nul
)

rem 2. Unpack the program (data\, logs\ and .env are not in the zip, so they survive an upgrade)
echo Unpacking program...
if not exist "C:\claudespace" mkdir "C:\claudespace"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -LiteralPath '%HERE%strait-watch.zip' -DestinationPath '%TARGET%' -Force"
if errorlevel 1 ( echo Unpack failed. & pause & exit /b 1 )
if exist "%HERE%VERSION.txt" copy /y "%HERE%VERSION.txt" "%TARGET%\VERSION.txt" >nul

rem 3. Optional .env in this folder: imported into the settings on first start
if exist "%HERE%.env" (
  echo Found .env next to the installer, copying it in for import.
  copy /y "%HERE%.env" "%TARGET%\.env" >nul
)

rem 4. Optional database snapshot: placed where the Backup page's Restore list finds it
if not exist "%TARGET%\data\backups" mkdir "%TARGET%\data\backups"
for %%F in ("%HERE%strait-share-*.db") do (
  echo Copying snapshot %%~nxF for Restore...
  copy /y "%%F" "%TARGET%\data\backups\%%~nxF" >nul
)

rem 5. PowerShell 7
where pwsh >nul 2>&1
if errorlevel 1 (
  echo Installing PowerShell 7 with winget...
  winget install --id Microsoft.PowerShell -e --accept-source-agreements --accept-package-agreements --disable-interactivity
  set "PATH=%ProgramFiles%\PowerShell\7;!PATH!"
  where pwsh >nul 2>&1 || ( echo PowerShell 7 could not be installed. Get it from https://aka.ms/powershell and run INSTALL.cmd again. & pause & exit /b 1 )
)

rem 6. Everything else: uv, NSSM, service account, permissions, firewall, service
echo.
pwsh -NoProfile -ExecutionPolicy Bypass -File "%TARGET%\setup.ps1" -Unattended %*
if errorlevel 1 ( echo. & echo setup.ps1 reported an error, see above. & pause & exit /b 1 )

echo.
echo === Done ===
echo 1. Log in with the login shown above (also in %TARGET%\data\initial-password.txt on a fresh install).
echo 2. Settings page: change the password, add Telegram and at least one LLM key.
echo 3. Backup page: Restore the snapshot to carry history over, or start fresh.
start "" http://localhost:8080/settings
pause
