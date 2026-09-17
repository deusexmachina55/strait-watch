"""Morning digest to Telegram: index, overnight change, top items, asset context, short LLM assessment."""
import logging
from datetime import date, datetime, timedelta, timezone

from app.alerts import telegram
from app.config import LOCAL_TZ, TZ_LABEL
from app.llm import chain
from app.web import data

log = logging.getLogger("uvicorn.error")
ASSETS = ["GC=F", "SI=F", "TSM", "^SOX", "NVDA", "BTC-USD", "CNH=X"]
SYSTEM = ("You write a two-sentence morning assessment of Taiwan Strait tension for a private investor, based only on the facts given. "
          "Plain text, no markdown, no hedging boilerplate, no advice to buy or sell.")


def build() -> str:
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    s = {r["day"]: r for r in data.rows("SELECT * FROM scores WHERE day IN (?, ?)", today, yesterday)}
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
    top = data.rows("SELECT i.source, i.title, a.title_en, a.severity, a.summary FROM item_analysis a JOIN items i ON i.id = a.item_id "
                    "WHERE i.published_utc >= ? ORDER BY a.severity DESC, i.published_utc DESC LIMIT 5", since)
    fired = data.rows("SELECT tripwire, detail FROM tripwire_log WHERE fired_utc >= ?", since)
    prices = data.prices(ASSETS)
    pla = data.pla(7)["latest"]

    lines = [f"Strait Watch morning digest, {datetime.now(LOCAL_TZ):%a %d %b %H:%M} {TZ_LABEL}"]
    cur, prev = s.get(today) or s.get(yesterday), s.get(yesterday)
    if cur:
        delta = f" ({cur['composite'] - prev['composite']:+.0f} vs yesterday)" if prev and cur is not prev else ""
        lines.append(f"Index {cur['composite']:.0f}{delta}: military {cur['military']:.0f}, economic {cur['economic']:.0f}, "
                     f"diplomatic {cur['diplomatic']:.0f}, rhetoric {cur['rhetoric']:.0f}")
    if pla:
        lines.append(f"PLA (MND {pla['report_date'][5:]}): {pla['aircraft']} aircraft, {pla['aircraft_entered']} entered airspace, "
                     f"{pla['navy_ships']} navy ships, {pla['official_ships']} official ships")
    lines.append("Tripwires 24h: " + (", ".join(f"{t['tripwire']}" for t in fired) if fired else "none"))
    lines.append("")
    lines.append("Top items:")
    for t in top:
        en = f" ({t['title_en']})" if t["title_en"] else ""
        lines.append(f"- [{t['severity']}] {t['title']}{en} ({t['source']})")
    if not top:
        lines.append("- none analyzed yet")
    lines.append("")
    lines.append("Assets 1d: " + ", ".join(f"{p['label']} {p['changes']['1d']:+.1f}%" for p in prices if p["changes"]["1d"] is not None))
    return "\n".join(lines)


def run() -> str:
    text = build()
    provider = None
    try:
        assessment, provider = chain.complete("digest", SYSTEM, text, json_mode=False)
        text += f"\n\nAssessment: {assessment[:600]}"
    except Exception as e:
        log.warning("digest assessment skipped: %r", e)
    telegram.send(text)
    from app.llm import briefs
    briefs.store_daily(text, provider)
    return "sent"
