Strait Watch installer
======================

This folder is the complete installer. Copy the whole folder (or a zip of it) to the target PC.

Contents
  INSTALL.cmd            run this, nothing else
  strait-watch.zip       the program, including its own Python runtime (built by Build-Package.ps1)
  strait-share-*.db      optional: database snapshot from the source PC, for Restore
  sample.env             optional: fill in and rename to .env to preload settings on first start
  VERSION.txt            build stamp
  Build-Package.ps1      for the source PC only, rebuilds this folder

Install (fresh or upgrade)
  1. Double-click INSTALL.cmd. Accept the administrator prompt.
  2. It unpacks the program to C:\claudespace\strait-watch, installs PowerShell 7, uv and NSSM if missing,
     creates the service account, sets folder permissions and the LAN firewall rule, installs and starts
     the Windows service, then prints the login and opens the Settings page.
     Internet is needed during install (winget downloads). Every system change is printed as it runs.
  3. Log in. On Settings: change the password, add a Telegram bot and at least one LLM key (Groq is
     enough), optionally an aisstream key for ships. Each field has a link and a test button.
  4. On Backup: set a destination folder and Test destination. To carry history over, pick the
     strait-share snapshot in Restore and press Restore. Or start from scratch: collectors backfill
     90 days of GDELT and MSA on their own.

Preloading settings with .env (optional)
  Copy sample.env to .env in this folder, fill in the values, run INSTALL.cmd. They are imported into
  the database on first start; the file can then be deleted from C:\claudespace\strait-watch.

Upgrade
  Run INSTALL.cmd from a newer installer folder. Data, logs and settings are kept.

Uninstall (administrator PowerShell 7)
  pwsh -NoProfile -ExecutionPolicy Bypass -File C:\claudespace\strait-watch\service.ps1 uninstall
  then delete C:\claudespace\strait-watch.

Requirements
  Windows 10 or 11, administrator rights, internet, a private LAN. The program must live at
  C:\claudespace\strait-watch (its bundled Python is bound to that path).
