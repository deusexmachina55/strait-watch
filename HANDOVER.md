# Handover

Read this first in a new session, after CLAUDE.md, SPEC.md and DECISIONS.md. It records what a fresh session cannot derive from the code. Append to it at the end of every phase; keep the "Current state" section accurate.

## Current state (2026-09-18, 02:50 SGT)
- Phases 1 to 4, the map (Phase 2b with AIS and ADS-B) and the watchdog are live on the service since the 01:14 reboot. All jobs green: 57 zones parsed on first run, AIS streaming (about 60 vessels per 30 min), ADS-B polling, translations caught up.
- Backup tab (Phase 5) built and tested on a copy, committed, needs a restart to go live. The user sets destination (drive or UNC path), schedule and retention in the browser; settings live in the `settings` table because the service cannot write `config.toml`. A local drive folder needs Modify rights for `svc-straitwatch` (icacls); a UNC path needs `BACKUP_SMB_USER` and `BACKUP_SMB_PASS` in `.env`. Nothing runs until the user enables it and a destination passes "Test destination". Restore is on the same page and works without a restart (scheduler paused, integrity check, safety copy, SQLite online backup into the live DB); tested: 100 deleted rows came back.
- Briefing tab (Phase 6) built and tested on a copy (a weekly brief generated via Groq in 7 s, grounded in the given numbers), committed, needs a restart. Briefs list shows the newest of each kind open, older ones collapsed, kind filter, last 40. Tab order now: Overview, Briefing, Signals, Markets, News, Warnings, Map, System, Backup.
- Settings tab (Phase 7) built and tested on a copy, committed, needs a restart. Secrets moved from `.env` to the `settings` table (password PBKDF2-hashed); on the first start after the restart the app imports the user's `.env` values automatically, then `.env` is redundant. `setup.ps1` no longer prompts for secrets; a fresh install gets a generated password in `data\initial-password.txt`.
- Packaging built in `installer\`: Build-Package.ps1 (zip with `.python` and `.venv`, about 300 MB, no data or secrets), install.cmd (admin bootstrap, installs pwsh 7, runs setup.ps1), upgrade.ps1 (snapshot, stop, replace, start), Export-ForSharing.ps1 (snapshot minus settings, keys, logs, briefs). setup.ps1 auto-detects interface and subnet from the default route. Only tested on this PC (build and export ran; install path untested on a second machine).
- First morning digest with LLM assessment due 07:30 SGT on 2026-09-18.

## How the box is run
- Windows service `StraitWatch` via NSSM (`C:\Program Files\nssm\nssm.exe`), runs as local user `svc-straitwatch`. Service account has Read and Execute on the repo, Modify only on `data\` and `logs\`, Read on `.env`. Python and packages live inside the repo (`.python\`, `.venv\`), installed with uv.
- Claude cannot run elevated commands. Restart is done by the user from an admin prompt:
  `pwsh -NoProfile -ExecutionPolicy Bypass -File C:\claudespace\strait-watch\service.ps1 restart`
  Windows PowerShell 5.1 blocks scripts, hence the explicit `pwsh` and `-ExecutionPolicy Bypass`. If the restart hangs, the user reboots the Dell; the service starts on its own after about 2 minutes (delayed auto start).
- `.env` is denied to Claude by `.claude/settings.json` (read and write). Since Phase 7 secrets live in the database (`settings` table) and are edited on `/settings`; `.env` is only imported once on first start. Test runs still pass dummy `TELEGRAM_*` and `BASIC_AUTH_*` env vars, which the copy database imports on its first start.
- `straitwatchtoken.txt` in the repo root holds the user's GitHub token. It is gitignored (`*token*.txt`). Do not read it.
- Git pushes use a fine-grained PAT stored in Git Credential Manager. Push after each commit; the user pre-approved pushes and everything else non-elevated (2026-09-18).
- Testing pattern: copy the live DB with `sqlite3` backup API into the scratchpad, set `STRAIT_DB` to the copy, dummy Telegram vars (`TELEGRAM_BOT_TOKEN=1:dummy`) so no real alerts go out, run collectors or start uvicorn on port 8081, check pages in the browser pane. Basic-auth URLs (`http://user:pass@127.0.0.1:8081/`) break `fetch()` on the map page; navigate once with credentials, then again without.
- Dashboard on the LAN: `http://192.168.0.101:8080/`, login `admin` plus the password the user set (imported from the old `.env`, changeable on `/settings`). The user's other PC is `dxm-r9`.

## Source quirks learned the hard way
- Gemini: the free tier only accepts the newest model names (`gemini-3.6-flash` as of 2026-09-18, fallback `gemini-3.1-flash-lite`); older names return 404 "no longer available to new users". Frequent 503 "high demand". Groq (`openai/gpt-oss-120b`) is the reliable fallback; its JSON mode rejects outputs above roughly 60 items, so batches are 40. OpenRouter free models rate-limit often.
- GDELT DOC API rate-limits the Dell's IP permanently after a burst; the raw 15-minute event files have no limit. Backfill of 90 days takes about 10 hours at 60 files per 2 minutes.
- MND lists only the last 9 daily reports; no archive. PLA baselines build from install day (2026-09-17).
- MSA warnings: English-titled entries are duplicates of the Chinese ones with dead detail links. Only `lang = 'zh'` rows are counted and parsed. Detail pages give zone corners in three coordinate formats (see `msa_zones.COORD`). About 5% have no coordinates (circles or points).
- CNH=X has no daily history on Yahoo; daily closes are derived from 5-minute bars.
- HYPE trades on Yahoo as `HYPE32196-USD`.
- Japan MOD press page: blocks non-browser user agents; `datetime` attributes are not zero-padded and occasionally malformed.
- aisstream.io coverage over the Strait is thin at night (about 20 vessels in 30 minutes); China Coast Guard hulls broadcast, PLA Navy does not. Static data (name, type) arrives separately, so classification improves over time.
- adsb.lol: `/v2/point/lat/lon/250` and `/v2/mil`; occasional read timeouts, one failed poll a day is normal.
- Windows service restart: `nssm restart` stalled twice; `service.ps1` now uses Stop-Service with a 30 s wait and a process-tree kill fallback.

## Design decisions not in SPEC or DECISIONS
- Index: sub-score = 50 + 15 × (0.6 × weighted mean z + 0.4 × max z), composite = weighted sub-scores. Baselines are 30/90-day, blended with priors that fade over 14 days. Count-type indicators for the current day use a trailing-24h estimate so the index does not collapse at midnight.
- Everything is served locally (Chart.js, htmx, Leaflet vendored under `app/web/static`, behind basic auth). Map tiles come from Esri World Ocean Base (free, attribution required); CARTO tiles need a key now.
- Translations: original text first, English in parentheses, on every page and in Telegram. Done by the LLM job (40 titles and 40 MSA warnings per 30 minutes) so new items lag by up to half an hour.
- Briefing: facts are pre-aggregated in `app/llm/briefs.py::facts` (index path and prior-period averages, PLA, zones, CCG, aircraft, tripwires, top 10 items, asset moves, Polymarket, mechanical signals). The prompt demands JSON with summary, watch list and a 14-day outlook (direction, confidence, rationale, triggers). `outlook_grade` compares the direction with the index 14 days later (flat band 5 points) into `outlook_scores`. The daily digest is stored as a daily brief. Mechanical signals: 7-day momentum, TSM minus SOX 20-day return ("risk premium"), Polymarket 30-day drift. Metaculus was checked and needs an account token, so it is out.
- Live-feed indicators (AIS, ADS-B) start counting the day after the feed came up, so a mid-day start does not read as a quiet day. adsb.lol returns 429 when polled too often (two processes polling during tests); the job now polls every 2 minutes and treats 429 as a skipped poll.
- Settings: `app/settings.py` (get, set_values, hash_password, import_env_once, ensure_admin, status). The AIS thread is supervised by the `ais_start` job every 5 minutes so a key added on the Settings page takes effect without a restart; LLM and Telegram read their keys per call. Timezone (`config.LOCAL_TZ`, `TZ_LABEL`) is read at import from the settings table, so it needs a restart.
- Pages auto-refresh via htmx partials (60 s news/markets/overview, 5 min warnings/signals, 30 s system); the map fetches `/api/map` every 60 s.

## User preferences
- Direct answers, no filler, no em dashes. Ask before elevated steps (they are the user's to run anyway).
- Every manual step needs the exact URL or click path.
- Prefers not to be woken by permission prompts; blanket approval given on 2026-09-18 for non-elevated work and pushes.
- Wants originals plus English, not English only.

## Packaging
- `installer\README.md` is the recipient's guide. The `.venv` is bound to `C:\claudespace\strait-watch`; install.cmd refuses any other path. `VERSION.txt` is written into the zip by the build (gitignored in the repo); `appersion.py` is the source of truth, shown on the System page.
- A recipient needs their own free accounts (Telegram bot, Groq at least, aisstream optional); the Settings page walks them through it. Do not ship the developer's keys.

## Next steps, in order
1. User restarts; changes nothing on Settings unless wanted (values imported from .env); sets the backup destination and runs "Backup now" once; presses "Weekly now" on Briefing.
2. First real test of install.cmd on a second PC when the user has one; expect small fixes.
3. Tuning after two weeks of data: priors in `config.toml`, tripwire thresholds, MSA zone kinds.
