# Installing Strait Watch on another PC

## What you need
- Windows 11 (or 10), administrator rights, an internet connection.
- A home network with a private subnet (192.168.x.x or 10.x.x.x).
- About 15 minutes. Optional accounts, all free, created afterwards from the Settings page: a Telegram bot (via @BotFather), a Groq API key, an aisstream.io key (GitHub login).

## Steps
1. Unzip `strait-watch-<version>.zip` so that the folder is exactly `C:\claudespace\strait-watch` (the bundled Python is bound to that path).
2. Right-click `C:\claudespace\strait-watch\installer\install.cmd`, choose **Run as administrator**.
   - It installs PowerShell 7 if missing, then runs `setup.ps1`.
   - `setup.ps1` prints each system change (network profile, NSSM, service account, folder permissions, firewall rule, service) and asks `y` before running it.
   - Optional parameters, if the auto-detected network is wrong: `install.cmd -InterfaceAlias "Wi-Fi" -LanSubnet 10.0.0.0/24 -Port 8080`.
3. At the end it prints the dashboard address and a generated login (also in `data\initial-password.txt`).
4. Open the dashboard in a browser, log in, go to **Settings**:
   - Change the password (this deletes the initial-password file).
   - Telegram: paste the bot token, Save and test, message the bot once, Detect chat, Send test message.
   - LLM: paste a Groq key (Gemini and OpenRouter optional), Save and test.
   - Ships: paste an aisstream key, Save and test.
5. Optional, to carry history over: on **Backup**, set a destination folder, Test destination, then in **Restore** pick the snapshot you were given (or browse to it) and press Restore. Your own settings from step 4 are kept.
6. Optional: on **Backup**, enable the nightly backup.

## Upgrading
Run as administrator: `pwsh -NoProfile -ExecutionPolicy Bypass -File C:\claudespace\strait-watch\installer\upgrade.ps1 -Zip <new zip>`. It snapshots the database, stops the service, replaces the code and runtime, keeps `data\` and `logs\`, restarts.

## Uninstalling
As administrator: `pwsh -NoProfile -ExecutionPolicy Bypass -File C:\claudespace\strait-watch\service.ps1 uninstall`, then delete the folder.

## For the person building the package
- `installer\Build-Package.ps1` on the development PC produces `dist\strait-watch-<version>-<git>.zip` (about 300 MB, includes the Python runtime; no data, no secrets).
- `installer\Export-ForSharing.ps1` produces `dist\strait-share-<date>.db`, a copy of the database with settings, keys, logs and briefs removed, for the recipient's Restore step.
- Bump `app\version.py` before building a release.
