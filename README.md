# Strait Watch

Personal early-warning dashboard for China/Taiwan/US tension in the Taiwan Strait. Monitoring only.
See SPEC.md for scope and DECISIONS.md for locked decisions and security requirements.

## Current phase
Phase 1: service skeleton, basic-auth dashboard, SQLite schema, scheduler heartbeat, Telegram test alert.

## Requirements
- Windows 11, PowerShell 7, winget, administrator rights for setup.
- A Telegram bot token (from @BotFather).

## Setup
Run from an elevated PowerShell 7 prompt:

```powershell
cd C:\claudespace\strait-watch
./setup.ps1
```

Optional parameters: `-InterfaceAlias 'Ethernet' -LanSubnet '192.168.0.0/24' -Port 8080`.

The script is idempotent. Each system-level change is printed and runs only after you confirm with `y`. It:
1. Installs uv if missing, a repo-local Python 3.12 in `.python\` and dependencies in `.venv\`.
2. Creates `data\`, `logs\` and `.env` (from `.env.example`), prompts for the bot token and dashboard credentials (hidden input), and reads the Telegram chat ID via `getUpdates` after you message the bot.
3. Sets the network profile to Private if needed.
4. Installs NSSM machine-wide (winget, or the official zip with SHA256 verification into `C:\Program Files\nssm\`).
5. Creates the local standard user `svc-straitwatch` with a random password that is not stored.
6. Applies ACLs: the service account has Read & Execute on the repo, Modify on `data\` and `logs\` only, Read on `.env`.
7. Creates an inbound firewall rule for TCP 8080, Private profile, LAN subnet only.
8. Installs and starts the `StraitWatch` service (delayed auto start, restart on failure, rotating log in `logs\service.log`).
9. Waits for `/health`, then sends a Telegram test alert.

Python is kept inside the repo (not the per-user install) so the service account needs no rights outside the repo folder.

## Operating
| Task | Command |
|---|---|
| Status | `./service.ps1 status` |
| Start / stop / restart (elevated) | `./service.ps1 restart` |
| Uninstall service, firewall rule and service account (elevated) | `./service.ps1 uninstall` |
| Logs | `logs\service.log` |

- Dashboard: `http://<dell-ip>:8080/` (basic auth, credentials in `.env`).
- Health JSON: `/health`. `status` is `ok` when the heartbeat ran within 180 seconds.
- Test alert: button on the dashboard, or `POST /api/test-alert` with header `X-Requested-With: strait-watch`.
- After code or dependency changes, re-run `./setup.ps1`: it syncs dependencies, reapplies ACLs and restarts the service.
- Changing service settings (port, arguments): `./service.ps1 uninstall`, then `./setup.ps1`.

## Security notes
- `.env`, `data\` and `logs\` are gitignored.
- The dashboard uses HTTP basic auth over plain HTTP on the LAN. Credentials are visible to anyone sniffing the LAN. Accepted for v1.
- The bot token is never written to logs: httpx request logging is suppressed and Telegram errors omit the URL.
