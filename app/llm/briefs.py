"""Daily, weekly and monthly briefs: pre-aggregated facts in, structured analyst text out, plus a graded outlook."""
import json
import logging
from contextlib import closing
from datetime import date, datetime, timedelta, timezone

from app import db
from app.alerts import telegram
from app.config import LOCAL_TZ, SETTINGS
from app.llm import chain
from app.web import data

log = logging.getLogger("uvicorn.error")
CFG = SETTINGS["briefs"]
ASSETS = ["GC=F", "SI=F", "TSM", "^SOX", "NVDA", "BTC-USD", "CNH=X", "TWD=X"]
GRADE_AFTER_DAYS = 14
FLAT_BAND = 5.0

SYSTEM = """You are an analyst writing a {kind} brief on Taiwan Strait tension for a private investor. Use only the facts given; every number you mention must appear in the input. No advice to buy or sell, no boilerplate, no headers with markdown symbols.
Return JSON only:
{{"summary": "<3 to 6 short paragraphs separated by blank lines: what changed, what stayed quiet, how the military, diplomatic, economic and rhetoric pictures compare to the prior period, what the markets did>",
  "watch": ["<3 to 5 concrete things to watch next, each one line>"],
  "outlook": {{"direction": "up|flat|down", "confidence": "low|medium|high", "horizon_days": 14,
               "rationale": "<two sentences tying the direction to specific numbers above>",
               "triggers": ["<2 or 3 events that would change this view>"]}}}}
The outlook is a judgment about the tension index over the next 14 days, not a forecast of conflict. Be sober."""


def period(kind: str, end: date | None = None) -> tuple[date, date]:
    end = end or datetime.now(LOCAL_TZ).date()
    if kind == "weekly":
        return end - timedelta(days=6), end
    if kind == "monthly":
        return end - timedelta(days=29), end
    return end, end


def facts(kind: str, start: date, end: date) -> dict:
    s, e = start.isoformat(), end.isoformat()
    prev_start = (start - (end - start) - timedelta(days=1)).isoformat()
    scores = data.rows("SELECT day, composite, military, economic, diplomatic, rhetoric FROM scores WHERE day BETWEEN ? AND ? ORDER BY day", s, e)
    prev = data.rows("SELECT AVG(composite) AS c, AVG(military) AS m, AVG(economic) AS e, AVG(diplomatic) AS d, AVG(rhetoric) AS r "
                     "FROM scores WHERE day BETWEEN ? AND ?", prev_start, (start - timedelta(days=1)).isoformat())[0]
    pla = data.rows("SELECT AVG(aircraft) AS avg_aircraft, MAX(aircraft) AS max_aircraft, AVG(navy_ships) AS avg_ships, MAX(navy_ships) AS max_ships, "
                    "SUM(median_line_crossed) AS days_median_line FROM pla_daily WHERE report_date BETWEEN ? AND ?", s, e)[0]
    zones = data.rows("SELECT COUNT(*) AS n, ROUND(SUM(area_km2)) AS km2, SUM(kind = 'live_fire') AS live_fire FROM msa_zones WHERE ok = 1 AND starts BETWEEN ? AND ?", s, e)[0]
    ccg = data.rows("SELECT COUNT(DISTINCT mmsi) AS n FROM ais_sightings WHERE cls = 'coast_guard' AND zone IN ('kinmen', 'matsu') AND day BETWEEN ? AND ?", s, e)[0]["n"]
    mil = data.rows("SELECT COUNT(DISTINCT hex) AS n FROM adsb_sightings WHERE day BETWEEN ? AND ?", s, e)[0]["n"]
    trips = data.rows("SELECT tripwire, detail FROM tripwire_log WHERE fired_utc BETWEEN ? AND ? AND tripwire NOT LIKE 'watchdog%'",
                      f"{s}T00:00:00", f"{e}T23:59:59")
    top = data.rows("SELECT i.source, COALESCE(i.title_en, a.title_en, i.title) AS title, a.severity, a.physical, a.summary FROM item_analysis a "
                    "JOIN items i ON i.id = a.item_id WHERE i.published_utc BETWEEN ? AND ? ORDER BY a.severity DESC, i.published_utc DESC LIMIT 10",
                    f"{s}T00:00:00", f"{e}T23:59:59")
    assets = {}
    for sym in ASSETS:
        closes = data.rows("SELECT day, close FROM prices_daily WHERE symbol = ? AND day BETWEEN ? AND ? ORDER BY day", sym, (start - timedelta(days=4)).isoformat(), e)
        if len(closes) >= 2:
            assets[data.LABELS.get(sym, sym)] = round((closes[-1]["close"] / closes[0]["close"] - 1) * 100, 1)
    odds = data.odds()
    odds_hist = data.odds_history(days=(end - start).days + 1)
    poly = [{"market": o["question"], "now_pct": round(o["probability"] * 100, 1)} for o in odds[:3]]
    for p, ser in zip(poly, odds_hist["series"]):
        first = next((v for v in ser["data"] if v is not None), None)
        p["change_pts"] = round(p["now_pct"] - first, 1) if first is not None else None
    mech = data.mechanical()
    return {
        "kind": kind, "period": f"{s} to {e}",
        "index": {"path": [{"day": r["day"][5:], "composite": round(r["composite"])} for r in scores],
                  "end": {k: round(scores[-1][k]) for k in ("composite", "military", "economic", "diplomatic", "rhetoric")} if scores else None,
                  "prior_period_avg": {k: round(v) for k, v in zip(("composite", "military", "economic", "diplomatic", "rhetoric"), (prev["c"], prev["m"], prev["e"], prev["d"], prev["r"])) if v is not None}},
        "pla_mnd": {k: (round(v, 1) if isinstance(v, float) else v) for k, v in pla.items()},
        "msa_closure_zones": dict(zones), "china_coast_guard_hulls_kinmen_matsu": ccg, "military_aircraft_seen_adsb": mil,
        "tripwires": trips, "top_items": top, "asset_change_pct": assets, "polymarket": poly,
        "mechanical": mech,
    }


def generate(kind: str, end: date | None = None, send: bool = True) -> str:
    start, end = period(kind, end)
    f = facts(kind, start, end)
    result, provider = chain.complete(f"brief_{kind}", SYSTEM.format(kind=kind), json.dumps(f, ensure_ascii=False))
    outlook = result.get("outlook") or {}
    direction = outlook.get("direction") if outlook.get("direction") in ("up", "flat", "down") else "flat"
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    index_now = f["index"]["end"]["composite"] if f["index"]["end"] else None
    with closing(db.connect()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO briefs (kind, period_start, period_end, created_utc, provider, summary, watch, outlook_direction, outlook_confidence, "
            "outlook_rationale, outlook_triggers, index_at, facts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (kind, start.isoformat(), end.isoformat(), now, provider, str(result.get("summary", "")).strip(),
             json.dumps(result.get("watch") or [], ensure_ascii=False), direction, outlook.get("confidence", "low"),
             str(outlook.get("rationale", "")).strip(), json.dumps(outlook.get("triggers") or [], ensure_ascii=False), index_now,
             json.dumps(f, ensure_ascii=False)))
        brief_id = cur.lastrowid
    if send:
        text = (f"Strait Watch {kind} brief, {start:%d %b} to {end:%d %b}\n\n{result.get('summary', '')}\n\nWatch:\n"
                + "\n".join(f"- {w}" for w in result.get("watch") or [])
                + f"\n\nOutlook (14 days, not a forecast): {direction}, {outlook.get('confidence', 'low')} confidence. {outlook.get('rationale', '')}")
        try:
            telegram.send(text[:3900])
        except Exception as e:
            log.warning("brief telegram failed: %r", e)
    return f"{kind} brief #{brief_id} ({provider}), outlook {direction}"


def store_daily(text: str, provider: str | None) -> None:
    today = datetime.now(LOCAL_TZ).date().isoformat()
    idx = data.rows("SELECT composite FROM scores WHERE day = ?", today)
    with closing(db.connect()) as conn, conn:
        conn.execute("INSERT INTO briefs (kind, period_start, period_end, created_utc, provider, summary, watch, outlook_direction, outlook_confidence, "
                     "outlook_rationale, outlook_triggers, index_at, facts) VALUES ('daily', ?, ?, ?, ?, ?, '[]', NULL, NULL, NULL, '[]', ?, '{}')",
                     (today, today, datetime.now(timezone.utc).isoformat(timespec="seconds"), provider, text,
                      round(idx[0]["composite"]) if idx else None))


def grade() -> str:
    """Grade weekly and monthly outlooks 14 days after they were written, against the index that followed."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=GRADE_AFTER_DAYS)).isoformat(timespec="seconds")
    due = data.rows("SELECT id, created_utc, outlook_direction, index_at FROM briefs WHERE kind IN ('weekly', 'monthly') "
                    "AND created_utc <= ? AND outlook_direction IS NOT NULL AND id NOT IN (SELECT brief_id FROM outlook_scores)", cutoff)
    graded = 0
    with closing(db.connect()) as conn, conn:
        for b in due:
            day = (datetime.fromisoformat(b["created_utc"]).astimezone(LOCAL_TZ).date() + timedelta(days=GRADE_AFTER_DAYS)).isoformat()
            after = data.rows("SELECT composite FROM scores WHERE day <= ? ORDER BY day DESC LIMIT 1", day)
            if not after or b["index_at"] is None:
                continue
            delta = after[0]["composite"] - b["index_at"]
            actual = "up" if delta >= FLAT_BAND else ("down" if delta <= -FLAT_BAND else "flat")
            conn.execute("INSERT INTO outlook_scores (brief_id, graded_utc, index_after, delta, actual_direction, hit) VALUES (?, ?, ?, ?, ?, ?)",
                         (b["id"], datetime.now(timezone.utc).isoformat(timespec="seconds"), after[0]["composite"], round(delta, 1), actual,
                          actual == b["outlook_direction"]))
            graded += 1
    return f"graded {graded}"


def weekly() -> str:
    return generate("weekly", datetime.now(LOCAL_TZ).date() - timedelta(days=1))


def monthly() -> str:
    return generate("monthly", datetime.now(LOCAL_TZ).date() - timedelta(days=1))
