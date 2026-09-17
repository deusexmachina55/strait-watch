"""Read queries for the web pages."""
from contextlib import closing
from datetime import date, datetime, timedelta, timezone

from app import db
from app.config import DB_PATH, LOCAL_TZ, SETTINGS

GROUPS = SETTINGS["prices"]["groups"]
LABELS = {s: l for g in GROUPS for s, l in zip(g["symbols"], g["labels"])}


def local(iso_utc: str, fmt: str = "%d %b %H:%M") -> str:
    return datetime.fromisoformat(iso_utc).astimezone(LOCAL_TZ).strftime(fmt)


def rows(sql: str, *params) -> list[dict]:
    with closing(db.connect()) as conn:
        return [dict(r) for r in conn.execute(sql, params)]


def days_back(n: int) -> list[str]:
    today = date.today()
    return [(today - timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]


# Indicators

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


def msa_warnings(region: str = "", military_only: bool = True, limit: int = 200) -> list[dict]:
    sql, params = "SELECT region, number, title, title_en, issued_date, url FROM msa_warnings WHERE 1=1", []
    if region:
        sql += " AND region = ?"
        params.append(region)
    if military_only:
        sql += " AND military = 1"
    sql += " ORDER BY issued_date DESC, number DESC LIMIT ?"
    return rows(sql, *params, limit)


# Prices

def prices(symbols: list[str], spark_days: int = 30) -> list[dict]:
    # Daily closes are keyed by UTC date (yfinance), so "today" must be the UTC date too
    today = datetime.now(timezone.utc).date()
    since = (today - timedelta(days=45)).isoformat()
    daily = rows("SELECT symbol, day, close FROM prices_daily WHERE day >= ? ORDER BY day", since)
    last = {r["symbol"]: r for r in rows("SELECT symbol, MAX(ts_utc) AS ts_utc, close FROM prices_intraday GROUP BY symbol")}
    out = []
    for symbol in symbols:
        closes = [(r["day"], r["close"]) for r in daily if r["symbol"] == symbol]
        latest = last.get(symbol)
        price = latest["close"] if latest else (closes[-1][1] if closes else None)
        # Reference closes: last full day (today's partial row excluded), then calendar lookbacks
        full = [c for c in closes if c[0] < today.isoformat()] if latest else closes[:-1]
        def ref(days_ago: int):
            cutoff = (today - timedelta(days=days_ago)).isoformat()
            return next((c for d, c in reversed(full) if d <= cutoff), None)
        refs = {"1d": full[-1][1] if full else None, "5d": ref(5), "30d": ref(30)}
        changes = {k: (price / v - 1) * 100 if price and v else None for k, v in refs.items()}
        spark_since = (today - timedelta(days=spark_days)).isoformat()
        out.append({"symbol": symbol, "label": LABELS.get(symbol, symbol), "price": price, "changes": changes,
                    "spark": [c for d, c in closes if d >= spark_since],
                    "as_of": local(latest["ts_utc"]) if latest else None})
    return out


def price_changes(symbols: list[str], days: int = 90) -> dict:
    """Percent change from the first close in the window, one series per symbol."""
    labels = days_back(days)
    out = {}
    for symbol in symbols:
        closes = {r["day"]: r["close"] for r in rows("SELECT day, close FROM prices_daily WHERE symbol = ? AND day >= ?", symbol, labels[0])}
        base = closes[min(closes)] if closes else None
        last, series = None, []
        for d in labels:
            last = closes.get(d, last)
            series.append(round((last / base - 1) * 100, 2) if last and base else None)
        out[symbol] = series
    return {"labels": labels, "series": out}


def fmt_price(p: float | None) -> str:
    if p is None:
        return "–"
    return f"{p:,.2f}" if p >= 10 else f"{p:.4f}"


# Scores and tripwires

def scores(days: int = 90) -> dict:
    labels = days_back(days)
    by_day = {r["day"]: r for r in rows("SELECT day, composite, military, economic, diplomatic, rhetoric FROM scores WHERE day >= ?", labels[0])}
    pick = lambda col: [round(by_day[d][col]) if d in by_day else None for d in labels]
    latest = by_day[max(by_day)] if by_day else None
    return {"labels": labels, "composite": pick("composite"), "latest": latest,
            "subs": {k: pick(k) for k in ("military", "economic", "diplomatic", "rhetoric")}}


def tripwires(limit: int = 30) -> list[dict]:
    out = rows("SELECT tripwire, fired_utc, severity, detail, url, alerted FROM tripwire_log ORDER BY fired_utc DESC LIMIT ?", limit)
    for r in out:
        r["when"] = local(r["fired_utc"])
    return out


# Items and notices

def odds() -> list[dict]:
    return rows("SELECT question, probability, volume, ts_utc FROM market_odds m WHERE ts_utc = "
                "(SELECT MAX(ts_utc) FROM market_odds WHERE market_id = m.market_id) ORDER BY volume DESC")


def odds_history(days: int = 90) -> dict:
    """Daily last probability per market, for the Signals chart."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    labels = days_back(days)
    markets = {}
    for r in rows("SELECT market_id, question, ts_utc, probability FROM market_odds WHERE ts_utc >= ? ORDER BY ts_utc", since):
        m = markets.setdefault(r["market_id"], {"question": r["question"], "by_day": {}})
        m["by_day"][local(r["ts_utc"], "%Y-%m-%d")] = round(r["probability"] * 100, 1)
    series = []
    for m in markets.values():
        last, points = None, []
        for d in labels:
            last = m["by_day"].get(d, last)
            points.append(last)
        series.append({"question": m["question"], "data": points})
    return {"labels": labels, "series": series}


def advisory_history() -> list[dict]:
    out = rows("SELECT country, level, published_utc, url, title FROM advisories ORDER BY published_utc DESC LIMIT 50")
    for r in out:
        r["when"] = r["published_utc"][:10]
    return out


def advisories() -> list[dict]:
    return rows("SELECT country, level, published_utc, url FROM advisories a WHERE rowid = (SELECT rowid FROM advisories "
                "WHERE country = a.country ORDER BY published_utc DESC, level DESC LIMIT 1) AND country IN ('Taiwan', 'China') "
                "ORDER BY country")


def sources() -> list[str]:
    return [r["source"] for r in rows("SELECT DISTINCT source FROM items ORDER BY source")]


def items(source: str = "", show_all: bool = False, q: str = "", limit: int = 60) -> list[dict]:
    sql, params = ("SELECT i.source, i.url, i.title, i.published_utc, i.tags, COALESCE(i.title_en, a.title_en) AS title_en, a.severity, a.category, a.summary "
                   "FROM items i LEFT JOIN item_analysis a ON a.item_id = i.id WHERE 1=1"), []
    if not show_all:
        sql += " AND i.relevant = 1"
    if source:
        sql += " AND i.source = ?"
        params.append(source)
    if q:
        sql += " AND (i.title LIKE ? OR i.title_en LIKE ? OR a.title_en LIKE ?)"
        params += [f"%{q}%"] * 3
    sql += " ORDER BY i.published_utc DESC LIMIT ?"
    out = rows(sql, *params, limit)
    for r in out:
        r["when"] = local(r["published_utc"])
        r["tags"] = [t for t in r["tags"].split(",") if t]
    return out


def notices(limit: int = 60) -> list[dict]:
    out = rows("SELECT i.source, i.url, i.title, i.published_utc, COALESCE(i.title_en, a.title_en) AS title_en, a.severity FROM items i "
               "LEFT JOIN item_analysis a ON a.item_id = i.id WHERE i.source IN ('Japan MOD', 'Taiwan Coast Guard') "
               "ORDER BY i.published_utc DESC LIMIT ?", limit)
    for r in out:
        r["when"] = local(r["published_utc"], "%d %b")
    return out


# System

def jobs() -> dict:
    now = datetime.now(timezone.utc)
    out = {}
    for r in rows("SELECT job, last_run_utc, ok, message FROM job_status ORDER BY job"):
        last = datetime.fromisoformat(r["last_run_utc"])
        out[r["job"]] = {"last_run": local(r["last_run_utc"], "%Y-%m-%d %H:%M:%S"),
                         "age_seconds": int((now - last).total_seconds()), "ok": bool(r["ok"]), "message": r["message"]}
    return out


def failures(limit: int = 20) -> list[dict]:
    out = rows("SELECT job, ran_at_utc, message FROM job_failures ORDER BY ran_at_utc DESC LIMIT ?", limit)
    for r in out:
        r["when"] = local(r["ran_at_utc"], "%Y-%m-%d %H:%M")
    return out


def db_size_mb() -> float:
    total = sum(p.stat().st_size for p in DB_PATH.parent.glob(DB_PATH.name + "*"))
    return round(total / 1_048_576, 1)


def llm_usage() -> dict:
    start = datetime.now(LOCAL_TZ).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    by = rows("SELECT provider, SUM(ok) AS ok, COUNT(*) - SUM(ok) AS failed FROM llm_calls WHERE ts_utc >= ? GROUP BY provider",
              start.isoformat(timespec="seconds"))
    return {"today": sum(r["ok"] + r["failed"] for r in by), "cap": SETTINGS["llm"]["daily_cap"], "providers": by,
            "analyzed": rows("SELECT COUNT(*) AS n FROM item_analysis")[0]["n"]}
