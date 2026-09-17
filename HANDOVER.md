# Handover

Read this first in a new session, after CLAUDE.md, SPEC.md and DECISIONS.md. It records what a fresh session cannot derive from the code. Append to it at the end of every phase; keep the "Current state" section accurate.

## Current state (2026-09-18, 01:10 SGT)
- Phases 1 to 4 done and live. Phase 2b (map, with the AIS and ADS-B "v2" layers) built and tested on a database copy, committed, **not yet running on the service**: needs one elevated restart by the user.
- Phase 5 remaining: nightly SQLite snapshot to the NAS (needs NAS share path and a dedicated NAS user from the user), log rotation (NSSM already rotates `logs\service.log` at 10 MB), status page (System page mostly covers it). The watchdog part of Phase 5 is done (in the map commit).
- Pending after restart: verify `msa_zones`, `adsb`, `ais`, `watchdog` jobs on the System page; check the Map tab; check the first morning digest (07:30 SGT).

## How the box is run
- Windows service `StraitWatch` via NSSM (`C:\Program Files\nssm\nssm.exe`), runs as local user `svc-straitwatch`. Service account has Read and Execute on the repo, Modify only on `data\` and `logs\`, Read on `.env`. Python and packages live inside the repo (`.python\`, `.venv\`), installed with uv.
- Claude cannot run elevated commands. Restart is done by the user from an admin prompt:
  `pwsh -NoProfile -ExecutionPolicy Bypass -File C:\claudespace\strait-watch\service.ps1 restart`
  Windows PowerShell 5.1 blocks scripts, hence the explicit `pwsh` and `-ExecutionPolicy Bypass`. If the restart hangs, the user reboots the Dell; the service starts on its own after about 2 minutes (delayed auto start).
- `.env` is denied to Claude by `.claude/settings.json` (read and write). The user edits it in Notepad. Keys present: Telegram, basic auth, Gemini, Groq, OpenRouter, aisstream.
- `straitwatchtoken.txt` in the repo root holds the user's GitHub token. It is gitignored (`*token*.txt`). Do not read it.
- Git pushes use a fine-grained PAT stored in Git Credential Manager. Push after each commit; the user pre-approved pushes and everything else non-elevated (2026-09-18).
- Testing pattern: copy the live DB with `sqlite3` backup API into the scratchpad, set `STRAIT_DB` to the copy, dummy Telegram vars (`TELEGRAM_BOT_TOKEN=1:dummy`) so no real alerts go out, run collectors or start uvicorn on port 8081, check pages in the browser pane. Basic-auth URLs (`http://user:pass@127.0.0.1:8081/`) break `fetch()` on the map page; navigate once with credentials, then again without.
- Dashboard on the LAN: `http://192.168.0.101:8080/`, login is `admin` plus the password in `.env`. The user's other PC is `dxm-r9`.

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
- Pages auto-refresh via htmx partials (60 s news/markets/overview, 5 min warnings/signals, 30 s system); the map fetches `/api/map` every 60 s.

## User preferences
- Direct answers, no filler, no em dashes. Ask before elevated steps (they are the user's to run anyway).
- Every manual step needs the exact URL or click path.
- Prefers not to be woken by permission prompts; blanket approval given on 2026-09-18 for non-elevated work and pushes.
- Wants originals plus English, not English only.

## Next steps, in order
1. User restarts the service; verify map layers and the four new jobs.
2. Phase 5: NAS backup (`VACUUM INTO` nightly, copy to SMB share with retention 7/4/12), which needs the share path and credentials from the user. Watchdog and log rotation already exist.
3. Tuning after two weeks of data: priors in `config.toml`, tripwire thresholds, MSA zone kinds.
