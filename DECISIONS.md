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

## Security requirements (apply from Phase 1)
- .gitignore covers .env, data/, logs/ before any push.
- GitHub auth via fine-grained PAT scoped to the strait-watch repo only. No full gh auth login.
- NSSM service runs as local standard user svc-straitwatch, not LocalSystem. Modify rights on repo folder only.
- Firewall: inbound TCP 8080, Private profile, remote address limited to the LAN subnet.
- HTTP basic auth on dashboard and API, credentials in .env.
- Show every elevated command before running it.
- Telegram chat ID fetched via getUpdates from PowerShell, never via browser URL.
