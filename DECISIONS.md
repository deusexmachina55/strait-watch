# Decisions log (from planning chat, 2026-09-17)

SPEC.md is the spec. This file records decisions made after it was written. Where they conflict, this file wins.

## Why this design
- Headline volume is a weak predictor. Taiwan Strait tension has been "high" in news since 2022. Focus on hard indicators (PLA counts, named exercises, mobilization, shipping/insurance, evacuation advisories) judged against rolling baselines.
- Markets reprice faster than news. The edge is slow, structural indicators retail ignores, not headline speed.
- Gold is the cleanest escalation trade. Tech (TSMC) is the most exposed. BTC has historically sold off with risk assets in crises, not acted as a haven.

## Locked decisions
- Host: old Dell, Windows 11 Pro, 24/7, orchestrator only. Quadro P1000 is NOT used. No local LLM.
- LLM: free cloud API chain only (Gemini -> Groq -> OpenRouter). No automating claude.ai or ChatGPT web tabs (ToS violation, fragile, rate limits).
- Remote access: RustDesk (2FA, strong permanent password). No Tailscale, no port forwarding.
- Dashboard: browser-based, LAN only.
- Alerts: Telegram. Bot token is set up.
- Trading: monitoring only. User trades manually on external platforms.
- Storage: SQLite on local SSD. Never run the live DB off the NAS share. NAS is backup target only, via a dedicated NAS user with write access to one share.
- Tooling is unrestricted. Claude Code chooses the best language/tool per job within the hard constraints.
- v1 scope: MND PLA counts, GDELT, RSS feeds, prices. AIS, NOTAMs, shipping insurance and backtesting are v2.
- Phase 2 source additions (2026-09-17, tested live from the Dell): US State Dept travel advisories, Japan Joint Staff press releases, Taiwan Coast Guard press releases, China MSA navigation warnings (Fujian, Zhejiang, Shanghai, Guangdong, Shandong channels on www.msa.gov.cn), Polymarket China/Taiwan markets.
- GDELT: raw 15-minute event export files from data.gdeltproject.org, not the DOC API. The API rate-limits the Dell's IP; the files have no limit and allow a 90-day backfill.
- Reuters, USNI News, Global Times and Xinhua are read through Google News RSS. Reuters has no public feed, USNI blocks scrapers, the other two feeds are stale.
- Map (2026-09-18): Leaflet vendored, Esri basemaps (Dark Gray with city labels by default, Light Gray, Ocean) plus OpenStreetMap, user-switchable; CARTO now requires a key. MSA zones parsed from Chinese-language detail pages only; English-titled entries are dead-link duplicates and are excluded from counts. AIS from aisstream.io, ADS-B from adsb.lol, both free, 7-day position retention.
- Map trails (2026-09-18): every received position is stored (ships throttled to one point per 30 s), kept 12 hours, drawn over a user-selected window. No change to polling rates, so no extra load on the free feeds.
- Watchdog (Phase 5 part) shipped with the map: Telegram alert when a job has no success within its allowed age, 12-hour cooldown.
- Phase 5 backup (2026-09-18): destination, schedule and retention are set in the browser and stored in the database (service cannot write config.toml); restore runs in place via SQLite online backup with a safety copy first. NAS credentials are entered on the Settings page.
- Phase 6 briefing (2026-09-18): weekly and monthly LLM briefs from pre-aggregated facts only, same free chain, with a 14-day outlook graded against the index afterwards. Labeled outlook, not forecast. No paid model. Metaculus rejected (needs an account token).
- Settings (2026-09-18): per-install secrets (login, Telegram, LLM keys, aisstream, NAS credentials, timezone) live in the database and are managed on the Settings page so the app can be installed by someone else without editing files. Password stored as PBKDF2 hash. `.env` is imported once for existing installs, then redundant. Chat ID detection still uses getUpdates server side, never a browser URL.
- Dropped: GDELT Global Frontpage Graph (unmaintained alpha), regional MSA "maritime news" pages (local port news), PLA Eastern Theater Command social media (fragile).

## Security requirements (apply from Phase 1)
- .gitignore covers .env, data/, logs/ before any push.
- GitHub auth via fine-grained PAT scoped to the strait-watch repo only. No full gh auth login.
- NSSM service runs as local standard user svc-straitwatch, not LocalSystem. Modify rights on repo folder only.
- Firewall: inbound TCP 8080, Private profile, remote address limited to the LAN subnet.
- HTTP basic auth on dashboard and API; the login is stored as a PBKDF2 hash in the database and changed on the Settings page (was .env until 2026-09-18).
- Show every elevated command before running it.
- Telegram chat ID fetched via getUpdates from PowerShell, never via browser URL.
