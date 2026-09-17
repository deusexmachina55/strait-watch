@echo off
setlocal
rem Strait Watch installer bootstrap. Run as administrator on the target PC after unzipping to C:\claudespace\strait-watch.
rem Installs PowerShell 7 if missing (winget), then runs setup.ps1 which does everything else and prints the login.

set "ROOT=%~dp0.."
for %%I in ("%ROOT%") do set "ROOT=%%~fI"

net session >nul 2>&1
if errorlevel 1 (
  echo This installer must run as administrator. Right-click install.cmd and choose "Run as administrator".
  pause
  exit /b 1
)

if /I not "%ROOT%"=="C:\claudespace\strait-watch" (
  echo Strait Watch must live at C:\claudespace\strait-watch ^(the bundled Python is bound to that path^).
  echo Current location: %ROOT%
  echo Move the folder there and run install.cmd again.
  pause
  exit /b 1
)

where pwsh >nul 2>&1
if errorlevel 1 (
  echo PowerShell 7 not found, installing with winget...
  winget install --id Microsoft.PowerShell -e --accept-source-agreements --accept-package-agreements --disable-interactivity
  if errorlevel 1 (
    echo winget could not install PowerShell 7. Install it from https://aka.ms/powershell and run install.cmd again.
    pause
    exit /b 1
  )
  set "PATH=%ProgramFiles%\PowerShell\7;%PATH%"
)

echo.
echo Running setup.ps1 (each system change asks for confirmation)...
pwsh -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\setup.ps1" %*
echo.
pause
