# Strait Watch

Personal early-warning dashboard for China/Taiwan/US tension in the Taiwan Strait. Monitoring only.
See SPEC.md for scope and DECISIONS.md for locked decisions and security requirements.

## Current phase
Phase 3: scoring and tripwires. Phases 1 (service) and 2 (collectors, dashboard) are done.

## Index and tripwires
- The `scoring` job runs every 10 minutes. It builds daily indicator series (PLA counts, MSA military warnings, GDELT conflict events, Japan and Coast Guard sightings, tagged item counts, Polymarket odds, State Dept level, 5-day returns for TSM, SOX, gold and CNH), compares each day with its trailing 30-day and 90-day baseline, and writes sub-scores (military, economic, diplomatic, rhetoric) and a composite 0-100 index per day to the `scores` table. 50 means "at baseline", each standard deviation adds 15 points.
- While an indicator has little history, a prior (typical 2024-2025 values in `config.toml`) stands in for the baseline and fades out over 14 days. MND history starts from install day, so PLA baselines are rough for the first month.
- Tripwires (`config.toml` `[tripwires]`): keyword hits on relevant items from the last 48 hours (named exercise, blockade, live-fire, no-fly zone, evacuation, mobilization), indicator spikes (PLA aircraft or navy above 2 sigma or an absolute floor, any Fujian military navigation warning, Polymarket jump), PLA spike during a named exercise, and composite index crossing 60, 75 and 90. Each has a cooldown in hours. A hit sends a Telegram message and is written to `tripwire_log`, shown on the dashboard.
- Weights, priors, thresholds and cooldowns are all in `config.toml`. Restart the service after editing.

## Data sources
| Source | Job | Schedule | Notes |
|---|---|---|---|
| Taiwan MND daily PLA report | `mnd` | hourly 08:05 to 18:05 SGT | aircraft, aircraft entering Taiwan airspace, median line, navy and official ships, balloons. Site keeps 9 days only |
| GDELT 2.0 event files | `gdelt`, `gdelt_backfill` | 15 min; backfill every 2 min until 90 days done | per-day counts for CHN-TWN, CHN-USA, CHN-JPN, CHN-PHL dyads |
| RSS and Google News | `rss` | 15 min | feeds in `config.toml` |
| Prices (yfinance) | `prices` | 5 min in market hours, 15 min otherwise | 5-minute bars kept 30 days, daily closes 2 years |
| China MSA navigation warnings | `msa` | hourly | Fujian, Zhejiang, Shanghai, Guangdong, Shandong. Military terms flagged. First run backfills 90 days (about 3 min) |
| Japan Joint Staff releases | `japan_mod` | 3 h | Chinese ship and aircraft movements near Japan |
| Taiwan Coast Guard releases | `coast_guard` | 3 h | China Coast Guard incursions |
| US State Dept advisories | `advisories` | 6 h | Taiwan and China levels |
| Polymarket | `polymarket` | hourly | active China/Taiwan markets, Yes probability |

Keyword groups, feeds, symbols and MSA channels are in `config.toml`. Restart the service after editing it.
Items are marked relevant when a keyword group matches together with a Taiwan context term (or an exercise name alone). The dashboard shows relevant items by default.

## Requirements
- Windows 11, PowerShell 7, winget, administrator rights for setup.
- A Telegram bot token (from @BotFather).

## Setup
Run from an elevated prompt (Windows PowerShell 5.1 blocks scripts, so call pwsh explicitly):

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File C:\claudespace\strait-watch\setup.ps1
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
| Status | `pwsh -ExecutionPolicy Bypass -File service.ps1 status` |
| Start / stop / restart (elevated) | `pwsh -ExecutionPolicy Bypass -File service.ps1 restart` |
| Uninstall service, firewall rule and service account (elevated) | `pwsh -ExecutionPolicy Bypass -File service.ps1 uninstall` |
| Logs | `logs\service.log` |

- Dashboard: `http://<dell-ip>:8080/` (basic auth, credentials in `.env`). Chart.js and htmx are served from `app/web/static`, no CDN.
- Health JSON: `/health`. `status` is `ok` when the heartbeat ran within 180 seconds.
- Test alert: button on the dashboard, or `POST /api/test-alert` with header `X-Requested-With: strait-watch`.
- After code or dependency changes, re-run `./setup.ps1`: it syncs dependencies, reapplies ACLs and restarts the service.
- Changing service settings (port, arguments): `./service.ps1 uninstall`, then `./setup.ps1`.

## Security notes
- `.env`, `data\` and `logs\` are gitignored.
- The dashboard uses HTTP basic auth over plain HTTP on the LAN. Credentials are visible to anyone sniffing the LAN. Accepted for v1.
- The bot token is never written to logs: httpx request logging is suppressed and Telegram errors omit the URL.
