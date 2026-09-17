"""Read queries for the web pages."""
from contextlib import closing
from datetime import date, datetime, timedelta, timezone

import json

from app import db, geo
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
    counts = rows("SELECT region, issued_date, COUNT(*) AS n FROM msa_warnings WHERE military = 1 AND lang = 'zh' AND issued_date >= ? "
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
    sql, params = ("SELECT w.region, w.number, w.title, w.title_en, w.issued_date, w.url, z.sea_area, z.window, z.ok AS mapped "
                   "FROM msa_warnings w LEFT JOIN msa_zones z ON z.url = w.url WHERE w.lang = 'zh'"), []
    if region:
        sql += " AND w.region = ?"
        params.append(region)
    if military_only:
        sql += " AND w.military = 1"
    sql += " ORDER BY w.issued_date DESC, w.number DESC LIMIT ?"
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


def mechanical() -> dict:
    """Rule-based signals for the Briefing page: index momentum, Taiwan risk premium, Polymarket drift."""
    sc = rows("SELECT day, composite, military, economic, diplomatic, rhetoric FROM scores ORDER BY day DESC LIMIT 8")
    momentum = {k: (round(sc[0][k] - sc[-1][k], 1) if len(sc) >= 2 else None) for k in ("composite", "military", "economic", "diplomatic", "rhetoric")}
    prem = risk_premium(60)
    o = odds()
    hist = odds_history(30)
    poly_change = None
    if o and hist["series"]:
        first = next((v for v in hist["series"][0]["data"] if v is not None), None)
        poly_change = round(o[0]["probability"] * 100 - first, 1) if first is not None else None
    return {"momentum": momentum, "risk_premium": prem["data"][-1] if prem["data"] else None,
            "poly_now": round(o[0]["probability"] * 100, 1) if o else None, "poly_question": o[0]["question"] if o else None, "poly_change": poly_change}


def risk_premium(days: int = 60, window: int = 20) -> dict:
    """TSM 20-day return minus SOX 20-day return, per day. Negative means TSM lags its sector."""
    since = (date.today() - timedelta(days=days + window + 10)).isoformat()
    tsm = {r["day"]: r["close"] for r in rows("SELECT day, close FROM prices_daily WHERE symbol = 'TSM' AND day >= ? ORDER BY day", since)}
    sox = {r["day"]: r["close"] for r in rows("SELECT day, close FROM prices_daily WHERE symbol = '^SOX' AND day >= ? ORDER BY day", since)}
    days_common = sorted(set(tsm) & set(sox))
    labels, out = [], []
    for i in range(window, len(days_common)):
        d, d0 = days_common[i], days_common[i - window]
        labels.append(d)
        out.append(round(((tsm[d] / tsm[d0]) - (sox[d] / sox[d0])) * 100, 2))
    return {"labels": labels[-days:], "data": out[-days:]}


def briefs(limit: int = 12) -> list[dict]:
    out = rows("SELECT b.*, s.actual_direction, s.delta, s.hit FROM briefs b LEFT JOIN outlook_scores s ON s.brief_id = b.id "
               "ORDER BY b.created_utc DESC LIMIT ?", limit)
    for b in out:
        b["when"] = local(b["created_utc"], "%d %b %H:%M")
        b["watch"] = json.loads(b["watch"] or "[]")
        b["triggers"] = json.loads(b["outlook_triggers"] or "[]")
        b["grade"] = {"actual_direction": b["actual_direction"], "delta": b["delta"], "hit": b["hit"]} if b["actual_direction"] else None
    return out


def scorecard() -> dict:
    g = rows("SELECT COUNT(*) AS n, COALESCE(SUM(hit), 0) AS hits FROM outlook_scores")[0]
    pending = rows("SELECT COUNT(*) AS n FROM briefs WHERE kind IN ('weekly', 'monthly') AND outlook_direction IS NOT NULL "
                   "AND id NOT IN (SELECT brief_id FROM outlook_scores)")[0]["n"]
    return {"graded": g["n"], "hits": g["hits"], "pending": pending}


def backups(limit: int = 30) -> list[dict]:
    out = rows("SELECT ts_utc, path, size_mb, ok, message FROM backups ORDER BY ts_utc DESC LIMIT ?", limit)
    for r in out:
        r["when"] = local(r["ts_utc"], "%Y-%m-%d %H:%M")
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


# Map

PLACES = [  # substring in a Japan MOD or Coast Guard title -> (lat, lon)
    ("宮古", (24.9, 125.2)), ("与那国", (24.45, 123.0)), ("大隅", (30.9, 131.0)), ("対馬", (34.3, 129.5)), ("奄美", (28.6, 129.2)),
    ("横当", (28.6, 129.2)), ("沖縄本島", (25.7, 126.5)), ("宗谷", (45.7, 142.0)), ("津軽", (41.5, 140.7)), ("沖永良部", (27.4, 128.6)),
    ("与論", (27.0, 128.4)), ("石垣", (24.4, 124.2)), ("尖閣", (25.75, 123.5)), ("硫黄島", (24.8, 141.3)), ("犬吠", (35.7, 141.0)),
    ("房総", (35.0, 140.5)), ("伊豆", (33.0, 139.5)), ("沖ノ鳥", (20.4, 136.1)), ("南大東", (25.8, 131.2)), ("久米", (26.3, 126.8)),
    ("金門", (24.44, 118.32)), ("馬祖", (26.16, 119.93)), ("澎湖", (23.57, 119.58)), ("東沙", (20.7, 116.72)), ("烏坵", (24.99, 119.45)),
]


def locate(source: str, title: str) -> tuple[float, float] | None:
    for key, pos in PLACES:
        if key in title:
            return pos
    if source == "Japan MOD" and "軍機" in title:
        return (27.5, 125.5)  # aircraft releases name no passage; East China Sea placeholder
    return None


def map_summary() -> dict:
    today = datetime.now(LOCAL_TZ).date().isoformat()
    now = datetime.now(timezone.utc)
    zones = rows("SELECT lat, lon, area_km2 FROM msa_zones WHERE ok = 1 AND starts <= ? AND ends >= ?", today, today)
    near = [z for z in zones if geo.haversine_km(z["lat"], z["lon"], *geo.TAIWAN) <= 300]
    ccg = rows("SELECT COUNT(DISTINCT mmsi) AS n FROM ais_sightings WHERE day = ? AND cls = 'coast_guard' AND zone IN ('kinmen', 'matsu')", today)[0]["n"]
    mil = rows("SELECT COUNT(DISTINCT hex) AS n FROM adsb_sightings WHERE day = ?", today)[0]["n"]
    latest = rows("SELECT civil, military FROM adsb_counts ORDER BY ts_utc DESC LIMIT 1")
    vessels = rows("SELECT COUNT(*) AS n FROM ais_vessels WHERE ts_utc >= ?", (now - timedelta(minutes=30)).isoformat(timespec="seconds"))[0]["n"]
    return {"zones_today": len(zones), "zone_area": round(sum(z["area_km2"] for z in near)), "ccg_kinmen": ccg, "mil_aircraft": mil,
            "civil_now": latest[0]["civil"] if latest else None, "mil_now": latest[0]["military"] if latest else None, "vessels_30m": vessels}


def map_data(day: str | None, days: int = 90) -> dict:
    since = (date.today() - timedelta(days=days)).isoformat()
    now = datetime.now(timezone.utc)
    zones = rows("SELECT z.url, z.region, z.number, z.kind, z.sea_area, z.starts, z.ends, z.window, z.polygon, z.area_km2, w.title, w.title_en "
                 "FROM msa_zones z JOIN msa_warnings w ON w.url = z.url WHERE z.ok = 1 AND z.ends >= ? ORDER BY z.starts", since)
    if day:
        zones = [z for z in zones if z["starts"] <= day <= z["ends"]]
    for z in zones:
        z["polygon"] = json.loads(z["polygon"])
    notices = rows("SELECT i.source, i.title, COALESCE(i.title_en, a.title_en) AS title_en, i.url, i.published_utc, a.severity FROM items i "
                   "LEFT JOIN item_analysis a ON a.item_id = i.id WHERE i.source IN ('Japan MOD', 'Taiwan Coast Guard') AND i.published_utc >= ?",
                   f"{since}T00:00:00+00:00")
    markers = []
    for n in notices:
        pos = locate(n["source"], n["title"])
        d = local(n["published_utc"], "%Y-%m-%d")
        if pos and (not day or d == day):
            markers.append({"source": n["source"], "title": n["title"], "title_en": n["title_en"], "url": n["url"], "day": d,
                            "severity": n["severity"], "lat": pos[0], "lon": pos[1]})
    recent = (now - timedelta(minutes=60)).isoformat(timespec="seconds")
    vessels = rows("SELECT mmsi, name, cls, lat, lon, sog, cog, ts_utc FROM ais_vessels WHERE ts_utc >= ?", recent)
    aircraft = rows("SELECT hex, flight, type, desc, military, lat, lon, alt, gs, track, ts_utc FROM adsb_aircraft WHERE ts_utc >= ?",
                    (now - timedelta(minutes=20)).isoformat(timespec="seconds"))
    track_since = (now - timedelta(hours=12)).isoformat(timespec="seconds")
    tracks = {}
    for r in rows("SELECT t.mmsi AS id, t.lat, t.lon FROM ais_tracks t JOIN ais_vessels v ON v.mmsi = t.mmsi "
                  "WHERE t.ts_utc >= ? AND v.cls IN ('coast_guard', 'military') ORDER BY t.mmsi, t.ts_utc", track_since):
        tracks.setdefault(f"v{r['id']}", []).append([r["lat"], r["lon"]])
    for r in rows("SELECT hex AS id, lat, lon FROM adsb_tracks WHERE ts_utc >= ? ORDER BY hex, ts_utc", track_since):
        tracks.setdefault(f"a{r['id']}", []).append([r["lat"], r["lon"]])
    for v in vessels + aircraft:
        v["age_min"] = int((now - datetime.fromisoformat(v["ts_utc"])).total_seconds() // 60)
    return {"day": day, "zones": zones, "markers": markers, "vessels": vessels, "aircraft": aircraft,
            "tracks": [p for p in tracks.values() if len(p) > 1], "generated": local(now.isoformat(), "%H:%M:%S")}
