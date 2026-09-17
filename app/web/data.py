"""Read queries for the dashboard."""
from contextlib import closing
from datetime import date, datetime, timedelta, timezone

from app import db
from app.config import LOCAL_TZ, SETTINGS


def local(iso_utc: str, fmt: str = "%d %b %H:%M") -> str:
    return datetime.fromisoformat(iso_utc).astimezone(LOCAL_TZ).strftime(fmt)


def rows(sql: str, *params) -> list[dict]:
    with closing(db.connect()) as conn:
        return [dict(r) for r in conn.execute(sql, params)]


def days_back(n: int) -> list[str]:
    today = date.today()
    return [(today - timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]


def pla(days: int = 90) -> dict:
    labels = days_back(days)
    by_day = {r["report_date"]: r for r in rows("SELECT * FROM pla_daily WHERE report_date >= ?", labels[0])}
    pick = lambda col: [by_day[d][col] if d in by_day else None for d in labels]
    return {"labels": labels, "aircraft": pick("aircraft"), "entered": pick("aircraft_entered"),
            "navy": pick("navy_ships"), "official": pick("official_ships"),
            "latest": by_day[max(by_day)] if by_day else None}


def gdelt(days: int = 90, dyad: str = "CHN-TWN") -> dict:
    labels = days_back(days)
    by_day = {r["day"]: r for r in rows("SELECT * FROM gdelt_daily WHERE dyad = ? AND day >= ?", dyad, labels[0])}
    pick = lambda col: [by_day[d][col] if d in by_day else None for d in labels]
    return {"labels": labels, "events": pick("events"), "material": pick("material_conflict"),
            "today": by_day.get(labels[-1])}


def msa(days: int = 90) -> dict:
    labels = days_back(days)
    counts = rows("SELECT region, issued_date, COUNT(*) AS n FROM msa_warnings WHERE military = 1 AND issued_date >= ? "
                  "GROUP BY region, issued_date", labels[0])
    regions = [c["region"] for c in SETTINGS["msa"]["channels"]]
    series = {r: dict.fromkeys(labels, 0) for r in regions}
    for c in counts:
        if c["region"] in series and c["issued_date"] in series[c["region"]]:
            series[c["region"]][c["issued_date"]] = c["n"]
    week_ago = labels[-7]
    last7 = sum(c["n"] for c in counts if c["issued_date"] >= week_ago)
    fujian7 = sum(c["n"] for c in counts if c["issued_date"] >= week_ago and c["region"] == "Fujian")
    return {"labels": labels, "series": [{"region": r, "data": list(series[r].values())} for r in regions],
            "last7": last7, "fujian7": fujian7}


def prices(days: int = 60) -> list[dict]:
    cfg = SETTINGS["prices"]
    since = (date.today() - timedelta(days=days)).isoformat()
    daily = rows("SELECT symbol, day, close FROM prices_daily WHERE day >= ? ORDER BY day", since)
    last = {r["symbol"]: r for r in rows("SELECT symbol, MAX(ts_utc) AS ts_utc, close FROM prices_intraday GROUP BY symbol")}
    out = []
    for symbol, label in zip(cfg["symbols"], cfg["labels"]):
        closes = [r["close"] for r in daily if r["symbol"] == symbol]
        latest = last.get(symbol)
        price = latest["close"] if latest else (closes[-1] if closes else None)
        # Change versus the previous daily close (today's partial close is excluded when intraday exists)
        ref = None
        if latest and len(closes) >= 1:
            ref = closes[-2] if len(closes) >= 2 and daily and daily[-1]["day"] == date.today().isoformat() else closes[-1]
        change = (price / ref - 1) * 100 if price and ref else None
        out.append({"symbol": symbol, "label": label, "price": price, "change": change, "spark": closes,
                    "as_of": local(latest["ts_utc"]) if latest else None})
    return out


def odds() -> list[dict]:
    return rows("SELECT question, probability, volume, ts_utc FROM market_odds m WHERE ts_utc = "
                "(SELECT MAX(ts_utc) FROM market_odds WHERE market_id = m.market_id) ORDER BY volume DESC")


def advisories() -> list[dict]:
    return rows("SELECT country, level, published_utc, url FROM advisories a WHERE rowid = (SELECT rowid FROM advisories "
                "WHERE country = a.country ORDER BY published_utc DESC, level DESC LIMIT 1) AND country IN ('Taiwan', 'China') "
                "ORDER BY country")


def sources() -> list[str]:
    return [r["source"] for r in rows("SELECT DISTINCT source FROM items ORDER BY source")]


def items(source: str = "", show_all: bool = False, limit: int = 60) -> list[dict]:
    sql = "SELECT source, url, title, published_utc, tags FROM items WHERE 1=1"
    params = []
    if not show_all:
        sql += " AND relevant = 1"
    if source:
        sql += " AND source = ?"
        params.append(source)
    sql += " ORDER BY published_utc DESC LIMIT ?"
    params.append(limit)
    out = rows(sql, *params)
    for r in out:
        r["when"] = local(r["published_utc"])
        r["tags"] = [t for t in r["tags"].split(",") if t]
    return out


def jobs() -> dict:
    now = datetime.now(timezone.utc)
    out = {}
    for r in rows("SELECT job, last_run_utc, ok, message FROM job_status ORDER BY job"):
        last = datetime.fromisoformat(r["last_run_utc"])
        out[r["job"]] = {"last_run": local(r["last_run_utc"], "%Y-%m-%d %H:%M:%S"),
                         "age_seconds": int((now - last).total_seconds()), "ok": bool(r["ok"]), "message": r["message"]}
    return out
