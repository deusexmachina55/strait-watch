# Strait Watch

Personal early-warning dashboard for China/Taiwan/US tension in the Taiwan Strait.
Purpose: spot escalation early to time moves in gold/silver, tech (TSMC, SOX, NVDA) and crypto (BTC).
Monitoring only. No trading, no broker integration.

Clarifying questions for this build were answered in a planning session. Answers are in this file and DECISIONS.md. Only ask about genuine gaps. DECISIONS.md overrides this file where they conflict.

## Host
- Old Dell, Windows 11 Pro, Xeon E-2124G, 16GB RAM, 256GB SSD. Runs 24/7.
- Orchestrator only. NO local LLM inference. All LLM work goes to free cloud APIs.
- Repo lives at C:\claudespace\strait-watch, GitHub account deusexmachina55.
- Dashboard viewed via browser on LAN (http://<dell-ip>:8080) or over RustDesk.
- Alerts via Telegram.
- NAS on LAN is the backup target only.

## Tooling
- Use any language, framework, library, tool or method that best achieves the objective.
- Suggested default: Python 3.12 + uv, FastAPI + Uvicorn, SQLite (WAL), APScheduler, Jinja2 + htmx + Chart.js, httpx, feedparser, selectolax, yfinance, NSSM for the Windows service.
- Pick the best tool per job. Mixing languages is fine where it clearly helps.
- Briefly note why when you deviate from the default.
- Hard constraints: runs 24/7 on this Windows box as a service and survives reboots, browser-based dashboard, no local LLM inference, no paid services or APIs without asking, all security requirements in DECISIONS.md.
- Config in .env (never committed), .env.example committed.

## Code rules
- Clean and pragmatic. No over-engineering, no excessive try/catch, minimal logging.
- One job failing must never crash the app. Catch at the job boundary only, log one line, move on.
- All timestamps stored in UTC. Display in Asia/Singapore.
- Never store raw HTML or full article pages. Title, URL, source, published time, snippet, tags, scores only.
- Dedup by canonical URL plus normalized title hash.
- Each collector is a small independent module. Scheduler wires them up.

## Suggested layout (guidance, adapt as needed)
```
strait-watch/
  CLAUDE.md, SPEC.md, DECISIONS.md, README.md
  setup.ps1            # one-shot installer, idempotent
  service.ps1          # install/uninstall/restart the service
  app/
    main, config, db, scheduler
    alerts/telegram
    collectors/        # mnd, gdelt, rss, prices
    scoring/           # baselines, index, tripwires
    llm/               # chain (gemini -> groq -> openrouter), prompts
    backup
    web/
  data/                # gitignored
  logs/                # gitignored
```

## Phases (build ONE phase at a time, stop and report when done)

### Phase 1: Skeleton + install + Telegram
- setup.ps1: install runtime and tools via winget if missing, install deps, create .env from .env.example if missing and prompt for secrets, create data/ and logs/, apply security baseline from DECISIONS.md (service user, firewall rule scoped to LAN subnet, basic auth), install and start the service (auto start, restart on failure), send a Telegram test message.
- Web app with a health page and a placeholder dashboard behind basic auth.
- SQLite schema created on startup.
- Scheduler running with a heartbeat job.
- Test-alert endpoint that sends a Telegram message.
- Done when: service survives a reboot, Telegram test arrives, dashboard loads from another LAN machine with a password prompt.

### Phase 2: Collectors
- MND: Taiwan MND daily PLA activity (aircraft, vessels, balloons, median line crossings). Daily, retry until published.
- GDELT: DOC 2.0 API, Taiwan Strait / PLA / blockade queries, article list + tone timeline. Every 30 min.
- RSS: configurable feed list (Reuters, Focus Taiwan, Taipei Times, SCMP, Xinhua, Global Times, USNI News, Defense News). Every 15 min.
- Prices: GC=F, SI=F, TSM, 2330.TW, ^SOX, NVDA, BTC-USD, CNH=X, DX-Y.NYB. Every 5 min in market hours, 15 min otherwise.
- Keyword pre-filter before marking articles relevant.
- Dashboard: latest items table, PLA count chart, price sparklines.
- Added (see DECISIONS.md): State Dept advisories (6h), Japan Joint Staff releases (3h), Taiwan Coast Guard releases (3h), China MSA navigation warnings with military-term flag (1h, 90-day backfill), Polymarket odds (1h). GDELT via event export files (15 min) with 90-day backfill.

### Phase 3: Scoring + tripwires
- Rolling baselines (30d and 90d mean/stddev) per indicator.
- Sub-scores: military, economic, diplomatic, rhetoric. Composite index 0-100.
- Tripwires: keywords (Joint Sword, Strait Thunder, live-fire, blockade, no-fly zone, evacuation advisory, quarantine, reserve mobilization) and combinations (PLA counts > 2 sigma AND named exercise, etc.).
- Tripwire fires -> Telegram alert, with per-tripwire cooldown.
- Dashboard: index gauge, index vs gold/TSM/BTC overlay chart, tripwire log.

### Phase 2b: Map (after Phase 3)
- Leaflet served locally, OpenStreetMap tiles. No paid map APIs.
- Layers: China MSA warning zones as polygons parsed from the warning detail text (live-fire and exercise zones highlighted), Japan Joint Staff sightings as markers on the named strait or passage, Taiwan Coast Guard incidents (Kinmen, Matsu), MND airspace sectors (north, central, southwest, east) shaded by aircraft count.
- Time slider or day picker; tripwire hits shown on the map once Phase 3 exists.
- No live ship or aircraft positions in v1 (AIS and ADS-B are v2).

### Phase 4: LLM layer
- Chain: Gemini free tier -> Groq -> OpenRouter free models. Fall through on rate limit or error. Keys in .env.
- Batch 20-30 filtered items per call. Strict JSON output: indicator category, severity 1-5, physical action vs rhetoric, novel vs rehash.
- Morning digest 07:30 Asia/Singapore to Telegram: index, overnight changes, top 5 items, asset context.
- Tripwire alerts get a short LLM summary appended.
- Hard daily call cap in config.

### Phase 5: Backup + hardening
- Nightly SQLite snapshot (VACUUM INTO), copy to NAS SMB share. Retention 7 daily, 4 weekly, 12 monthly.
- Log rotation.
- Watchdog: Telegram alert if any collector has not succeeded in N hours.
- Status page: last run per job, LLM calls used today, DB size, last backup.

## Working agreement
- Build only the phase asked for. Run it, test it, fix it, then commit and push.
- Keep README.md updated with setup and operating notes.
