# Strait Watch

Personal early-warning dashboard for China/Taiwan/US tension in the Taiwan Strait. Monitoring only.
See SPEC.md for scope and DECISIONS.md for locked decisions and security requirements.

## Current phase
Phase 2b (map with live AIS and ADS-B layers) and the Phase 5 watchdog are done. Phases 1 to 4 are done. Remaining: Phase 5 NAS backup. See HANDOVER.md for operational notes.

## Map
- `/map`: Leaflet (vendored) on Esri World Ocean Base tiles. Layers: MSA closure zones (polygons parsed from the Chinese warning detail pages by the `msa_zones` job, colored by kind), Japan Joint Staff and Coast Guard notices placed on the named passage or island, coast guard and naval ships, tankers, other ships (AIS, last 60 minutes), military and civil aircraft (ADS-B, last 20 minutes), 12-hour tracks for coast guard, naval and military aircraft. Day picker or all 90 days. Refreshes every minute.
- `ais` runs as a background thread on the aisstream.io websocket (key `AISSTREAM_API_KEY` in `.env`; without it the job reports "not set" and the layer stays empty). Box 20.5 to 27.5 N, 115.5 to 123.5 E. Keeps the latest position per vessel (7 days), 10-minute tracks for flagged classes, and per-day sightings in named areas (Kinmen, Matsu, Taiwan ports, the Strait box).
- `adsb` polls adsb.lol every minute: civil aircraft within 250 nm of the Strait and all military aircraft in the region. Keeps 7 days of positions and 90 days of counts.
- Derived indicators feed the index: closed-zone area within 300 km of Taiwan, zones in the Strait, China Coast Guard hulls at Kinmen/Matsu and in the Strait, tankers at Taiwan ports, military aircraft per day, mean civil traffic. Tripwires: any zone in the Strait, CCG surge at Kinmen, military aircraft surge.
- English-titled MSA entries are duplicates of the Chinese ones with dead links; only Chinese entries (`lang = 'zh'`) are counted, translated and mapped.

## Briefing
- `/briefing`: numbers first, prose second. Tiles: 7-day momentum of the index and sub-scores, Taiwan risk premium (TSM 20-day return minus SOX 20-day return, negative means markets price Taiwan-specific risk), Polymarket 30-day change, outlook scorecard. Chart: risk premium over 60 days.
- Briefs list: the newest daily, weekly and monthly brief are shown open; older ones collapse to a one-line header (click to expand), with a filter by kind. The page shows the last 40; all briefs stay in the database.
- Briefs: the daily digest (07:30 SGT) is stored as the daily brief. Weekly briefs run Mondays 08:00 SGT for the previous 7 days, monthly on the 1st for the previous 30 (`config.toml` `[briefs]`). "Weekly now" and "Monthly now" generate one for the trailing period. Every brief goes to Telegram.
- Input to the LLM is pre-aggregated facts only (index path and prior-period averages, PLA counts, closure zones, coast guard hulls, military aircraft, tripwires, top 10 items by severity, asset moves, Polymarket, the mechanical signals). The prompt requires every number to come from the input. Same free chain as the rest.
- Outlook: each weekly and monthly brief ends with a direction (up, flat, down) for the index over 14 days, a confidence and triggers. The `outlook_grade` job compares it with the index 14 days later (flat band of 5 points) and the scorecard shows the hit rate. This is the check on whether the LLM's read carries any signal. It is an outlook, not a forecast of conflict.

## Backup
- `/backup` page: set the destination folder (local drive or UNC path), nightly time (SGT), retention (7 daily, 4 weekly on Sundays, 12 monthly on the 1st) and enable it. Buttons: Test destination, Backup now. Settings are stored in the database (the service cannot write `config.toml`).
- Each run writes a `VACUUM INTO` snapshot to `data\backups\` (last 3 kept), then copies it to `<destination>\daily\`, and to `weekly\` or `monthly\` on those days. The `backup` job checks every 5 minutes whether tonight's run is due.
- Local drive: the service account `svc-straitwatch` needs Modify on the folder, e.g. `icacls "D:\Backups\strait-watch" /grant svc-straitwatch:(OI)(CI)M` from an admin prompt.
- UNC path: put a NAS user with write access to that share in `.env` as `BACKUP_SMB_USER` and `BACKUP_SMB_PASS`, restart the service. The job runs `net use` before copying.
- Restore: on the Backup page, pick a snapshot (local, or from the destination's daily/weekly/monthly folders) or type a path, and press Restore. No restart: collectors pause, the snapshot is integrity-checked, a safety copy of the current database goes to `data\backups\*-pre-restore.db`, the snapshot is copied into the live database with SQLite's online backup API, collectors resume.
- Fresh install with history: run `setup.ps1`, open the Backup page, set the destination, Test destination, Restore the newest snapshot.

## Watchdog
The `watchdog` job (every 30 minutes) sends a Telegram alert when a job has had no successful run within its allowed age (`config.toml` `[watchdog.max_age_hours]`), one alert per job per 12 hours. Alerts are listed in the tripwire log as `watchdog_<job>`.

## LLM layer
- Chain in `config.toml` `[llm]`: Gemini, then Groq, then OpenRouter, all free tiers. A provider is skipped when its key is missing from `.env`, and the next one is tried on any error or rate limit. Every call is logged in `llm_calls`; a hard daily cap (`daily_cap`, SGT day) stops all LLM work when reached.
- `llm_analyze` job (every 30 min): sends up to `batch_size` unanalyzed relevant items from the last `max_item_age_days` days and gets back category, severity 1 to 5, physical vs rhetoric, novel vs rehash, an English title for non-English items and a one-line summary (`item_analysis`). It also translates untranslated military MSA warning titles (40 per run). Translations show in parentheses on the News and Warnings pages.
- Severity feeds the index: daily sums of physical-item severity go into the military sub-score, the rest into rhetoric (`llm_physical`, `llm_rhetoric` in `[scoring]`).
- Keyword tripwire alerts get a two-sentence LLM summary when a provider answers; alerts never wait on a failed provider.
- `digest` job at 07:30 SGT (`digest_hour`, `digest_minute`): Telegram message with the index and its change, PLA counts, tripwires in the last 24 h, top 5 items by severity, 1-day asset moves, and a two-sentence LLM assessment.
- The System page shows calls today per provider against the cap.

## Pages
| Page | Content | Auto-refresh |
|---|---|---|
| `/` Overview | Tension index and sub-scores, key tiles, key prices, PLA/MSA/GDELT charts, recent tripwires and items | 60 s |
| `/news` | All items with source filter, title search, relevance toggle | 60 s |
| `/markets` | Instrument groups from `config.toml` (`[[prices.groups]]`): last price, 1d/5d/30d change, 30-day sparkline, 90-day comparison chart per group | 60 s |
| `/warnings` | China MSA navigation warnings (region filter, military only), Japan Joint Staff and Coast Guard notices, full tripwire log | 5 min |
| `/signals` | Polymarket odds with 90-day probability chart, US State Department advisories with history | 5 min |
| `/system` | Service status, DB size, job table, recent failures, Telegram test button | 30 s |

Refresh is done by htmx polling the `/partials/*` endpoints and swapping the page section, no full reload. Prices are only as fresh as the `prices` job (5 minutes in market hours).

## Index and tripwires
- The `scoring` job runs every 10 minutes. It builds daily indicator series (PLA counts, MSA military warnings, GDELT conflict events, Japan and Coast Guard sightings, tagged item counts, Polymarket odds, State Dept level, 5-day returns for TSM, SOX, gold and CNH), compares each day with its trailing 30-day and 90-day baseline, and writes sub-scores (military, economic, diplomatic, rhetoric) and a composite 0-100 index per day to the `scores` table. 50 means "at baseline", each standard deviation adds 15 points.
- Per-day event counts (MSA, GDELT, notices, tagged items, LLM severity) are partial for the current day, so today's value is estimated as the trailing 24 hours: yesterday's count scaled by the part of the day not yet elapsed, plus today so far. This keeps the index continuous across midnight.
- While an indicator has little history, a prior (typical 2024-2025 values in `config.toml`) stands in for the baseline and fades out over 14 days. MND history starts from install day, so PLA baselines are rough for the first month.
- Tripwires (`config.toml` `[tripwires]`): keyword hits on relevant items from the last 48 hours (named exercise, blockade, live-fire, no-fly zone, evacuation, mobilization), indicator spikes (PLA aircraft or navy above 2 sigma or an absolute floor, any Fujian military navigation warning, Polymarket jump), PLA spike during a named exercise, and composite index crossing 60, 75 and 90. Each has a cooldown in hours. A hit sends a Telegram message and is written to `tripwire_log`, shown on the dashboard.
- Weights, priors, thresholds and cooldowns are all in `config.toml`. Restart the service after editing.

## Data sources
| Source | Job | Schedule | Notes |
|---|---|---|---|
| Taiwan MND daily PLA report | `mnd` | hourly 08:05 to 18:05 SGT | aircraft, aircraft entering Taiwan airspace, median line, navy and official ships, balloons. Site keeps 9 days only |
| GDELT 2.0 event files | `gdelt`, `gdelt_backfill` | 15 min; backfill every 2 min until 90 days done | per-day counts for CHN-TWN, CHN-USA, CHN-JPN, CHN-PHL dyads |
| RSS and Google News | `rss` | 15 min | feeds in `config.toml` |
| Prices (yfinance) | `prices` | 5 min in market hours, 15 min otherwise | 30 instruments in `config.toml` groups (Taiwan/semis, Mag 7, commodities, crypto, FX). 5-minute bars kept 30 days, daily closes 2 years |
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
- Test alert: button on the System page, or `POST /api/test-alert` with header `X-Requested-With: strait-watch`.
- `service.ps1 restart` stops via the service manager, waits up to 30 s, kills the process tree if still pending, then starts.
- After code or dependency changes, re-run `./setup.ps1`: it syncs dependencies, reapplies ACLs and restarts the service.
- Changing service settings (port, arguments): `./service.ps1 uninstall`, then `./setup.ps1`.

## Security notes
- `.env`, `data\` and `logs\` are gitignored.
- The dashboard uses HTTP basic auth over plain HTTP on the LAN. Credentials are visible to anyone sniffing the LAN. Accepted for v1.
- The bot token is never written to logs: httpx request logging is suppressed and Telegram errors omit the URL.
