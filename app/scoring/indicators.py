"""Daily indicator series built from collected data. Values keyed by local (SGT) day."""
from collections import defaultdict
from datetime import date, datetime, timedelta

from app import geo
from app.config import LOCAL_TZ
from app.web.data import rows

STATE_MEDIA = ("Global Times", "Xinhua")
NEWS_EXCLUDED = ("Japan MOD", "Taiwan Coast Guard")
PRICE_RETURNS = {"tsm_5d": "TSM", "gold_5d": "GC=F", "cnh_5d": "CNH=X", "sox_5d": "^SOX"}
# Level-type indicators (complete whenever present), as opposed to per-day event counts
PARTIAL_DAY_EXEMPT = {"pla_aircraft", "pla_entered", "pla_navy", "pla_official", "polymarket", "advisory", "civil_traffic",
                      "zone_area_taiwan", "zone_in_strait", *PRICE_RETURNS}


def local_day(iso_utc: str) -> str:
    return datetime.fromisoformat(iso_utc).astimezone(LOCAL_TZ).date().isoformat()


def next_day(first: str | None, utc: bool = False) -> str | None:
    if not first:
        return None
    d = local_day(first) if utc else first
    return (date.fromisoformat(d) + timedelta(days=1)).isoformat()


def zero_fill(counts: dict[str, float], labels: list[str], first_day: str | None) -> dict[str, float]:
    """Counts are zero on days with coverage but no events. Before coverage starts they are unknown."""
    if not first_day:
        return {}
    return {d: counts.get(d, 0) for d in labels if d >= first_day}


def series(days: int = 120) -> dict[str, dict[str, float]]:
    today = date.today()
    labels = [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    since = labels[0]
    out: dict[str, dict[str, float]] = defaultdict(dict)

    for r in rows("SELECT * FROM pla_daily WHERE report_date >= ?", since):
        d = r["report_date"]
        out["pla_aircraft"][d] = r["aircraft"]
        out["pla_entered"][d] = r["aircraft_entered"]
        out["pla_navy"][d] = r["navy_ships"]
        out["pla_official"][d] = r["official_ships"]

    msa = rows("SELECT region, issued_date, COUNT(*) AS n FROM msa_warnings WHERE military = 1 AND lang = 'zh' AND issued_date >= ? "
               "GROUP BY region, issued_date", since)
    msa_first = rows("SELECT MIN(issued_date) AS d FROM msa_warnings")[0]["d"]
    total, fujian = defaultdict(int), defaultdict(int)
    for r in msa:
        total[r["issued_date"]] += r["n"]
        if r["region"] == "Fujian":
            fujian[r["issued_date"]] += r["n"]
    out["msa_military"] = zero_fill(total, labels, msa_first)
    out["msa_fujian_military"] = zero_fill(fujian, labels, msa_first)

    gdelt = rows("SELECT day, dyad, verbal_conflict, material_conflict FROM gdelt_daily WHERE day >= ?", since)
    gdelt_first = rows("SELECT MIN(day) AS d FROM gdelt_daily")[0]["d"]
    twn_mat, twn_verb, usa_mat = {}, {}, {}
    for r in gdelt:
        if r["dyad"] == "CHN-TWN":
            twn_mat[r["day"]], twn_verb[r["day"]] = r["material_conflict"], r["verbal_conflict"]
        elif r["dyad"] == "CHN-USA":
            usa_mat[r["day"]] = r["material_conflict"]
    out["gdelt_twn_material"] = zero_fill(twn_mat, labels, gdelt_first)
    out["gdelt_twn_verbal"] = zero_fill(twn_verb, labels, gdelt_first)
    out["gdelt_usa_material"] = zero_fill(usa_mat, labels, gdelt_first)

    items = rows("SELECT source, published_utc, tags, relevant FROM items WHERE published_utc >= ?",
                 f"{since}T00:00:00+00:00")
    items_first = rows("SELECT MIN(fetched_utc) AS d FROM items")[0]["d"]
    items_first = local_day(items_first) if items_first else None
    counts = {k: defaultdict(int) for k in ("japan_sightings", "cga_incursions", "items_military", "items_diplomatic",
                                            "items_economic", "items_state_media")}
    for r in items:
        d, tags = local_day(r["published_utc"]), set(r["tags"].split(","))
        if r["source"] == "Japan MOD":
            counts["japan_sightings"][d] += 1
        elif r["source"] == "Taiwan Coast Guard":
            counts["cga_incursions"][d] += "coast_guard" in tags
        elif r["relevant"]:
            counts["items_military"][d] += bool(tags & {"military", "blockade", "mobilization", "exercise_name"})
            counts["items_diplomatic"][d] += "diplomatic" in tags
            counts["items_economic"][d] += "economic" in tags
            counts["items_state_media"][d] += r["source"] in STATE_MEDIA
    # News feeds only cover the last few days before first fetch; count from first fetch day
    for k, c in counts.items():
        out[k] = zero_fill(c, labels, items_first)

    # LLM severity sums per day: physical actions feed military, the rest feeds rhetoric
    analyzed = rows("SELECT a.severity, a.physical, i.published_utc FROM item_analysis a JOIN items i ON i.id = a.item_id "
                    "WHERE i.published_utc >= ?", f"{since}T00:00:00+00:00")
    llm_first = rows("SELECT MIN(analyzed_utc) AS d FROM item_analysis")[0]["d"]
    physical, rhetoric = defaultdict(int), defaultdict(int)
    for r in analyzed:
        (physical if r["physical"] else rhetoric)[local_day(r["published_utc"])] += r["severity"]
    out["llm_physical"] = zero_fill(physical, labels, local_day(llm_first) if llm_first else None)
    out["llm_rhetoric"] = zero_fill(rhetoric, labels, local_day(llm_first) if llm_first else None)

    # Closure zones: area within 300 km of Taiwan and count in the Strait, per active day
    zones = rows("SELECT starts, ends, lat, lon, area_km2 FROM msa_zones WHERE ok = 1 AND ends >= ?", since)
    zones_first = rows("SELECT MIN(parsed_utc) AS d FROM msa_zones")[0]["d"]
    area, in_strait = defaultdict(float), defaultdict(int)
    for z in zones:
        near = geo.haversine_km(z["lat"], z["lon"], *geo.TAIWAN) <= 300
        strait = geo.in_box(z["lat"], z["lon"]) or geo.in_circle(z["lat"], z["lon"], "kinmen") or geo.in_circle(z["lat"], z["lon"], "matsu")
        d = date.fromisoformat(z["starts"])
        while d <= date.fromisoformat(z["ends"]):
            if near:
                area[d.isoformat()] += z["area_km2"]
            in_strait[d.isoformat()] += strait
            d += timedelta(days=1)
    # Zones are known for the whole window once the first parse ran, so coverage starts at the window start
    out["zone_area_taiwan"] = zero_fill(area, labels, since if zones_first else None)
    out["zone_in_strait"] = zero_fill(in_strait, labels, since if zones_first else None)

    # AIS sightings: distinct hulls per day per named area
    # Live feeds start mid-day; the first partial day would read as an artificially quiet day
    ais_first = next_day(rows("SELECT MIN(day) AS d FROM ais_sightings")[0]["d"])
    ccg_k, ccg_s, tank = defaultdict(set), defaultdict(set), defaultdict(set)
    for r in rows("SELECT day, mmsi, zone, cls FROM ais_sightings WHERE day >= ?", since):
        if r["cls"] == "coast_guard" and r["zone"] in ("kinmen", "matsu"):
            ccg_k[r["day"]].add(r["mmsi"])
        if r["cls"] == "coast_guard" and r["zone"] == "strait":
            ccg_s[r["day"]].add(r["mmsi"])
        if r["cls"] == "tanker" and r["zone"] == "taiwan_port":
            tank[r["day"]].add(r["mmsi"])
    out["ccg_near_kinmen"] = zero_fill({d: len(v) for d, v in ccg_k.items()}, labels, ais_first)
    out["ccg_strait"] = zero_fill({d: len(v) for d, v in ccg_s.items()}, labels, ais_first)
    out["tankers_ports"] = zero_fill({d: len(v) for d, v in tank.items()}, labels, ais_first)

    # ADS-B: distinct military aircraft per day, mean civil count per day
    adsb_first = next_day(rows("SELECT MIN(ts_utc) AS d FROM adsb_counts")[0]["d"], utc=True)
    mil = {r["day"]: r["n"] for r in rows("SELECT day, COUNT(DISTINCT hex) AS n FROM adsb_sightings WHERE day >= ? GROUP BY day", since)}
    out["mil_aircraft"] = zero_fill(mil, labels, adsb_first)
    civil = defaultdict(list)
    for r in rows("SELECT ts_utc, civil FROM adsb_counts WHERE ts_utc >= ?", f"{since}T00:00:00+00:00"):
        civil[local_day(r["ts_utc"])].append(r["civil"])
    # Traffic is much lower at night, so a day only counts once most of it is sampled (one sample a minute)
    out["civil_traffic"] = {d: sum(v) / len(v) for d, v in civil.items() if len(v) >= 450}

    # Polymarket: daily last probability (in percent) of the highest-volume market
    top = rows("SELECT market_id FROM market_odds ORDER BY volume DESC LIMIT 1")
    if top:
        for r in rows("SELECT ts_utc, probability FROM market_odds WHERE market_id = ? ORDER BY ts_utc", top[0]["market_id"]):
            out["polymarket"][local_day(r["ts_utc"])] = r["probability"] * 100

    # State Dept advisory level for Taiwan, carried forward (0 = level 1)
    advisories = rows("SELECT level, published_utc FROM advisories WHERE country = 'Taiwan' ORDER BY published_utc")
    for d in labels:
        level = next((a["level"] for a in reversed(advisories) if local_day(a["published_utc"]) <= d), None)
        if level:
            out["advisory"][d] = level - 1

    # 5-trading-day percent returns from daily closes
    for name, symbol in PRICE_RETURNS.items():
        closes = rows("SELECT day, close FROM prices_daily WHERE symbol = ? AND day >= ? ORDER BY day", symbol,
                      (today - timedelta(days=days + 10)).isoformat())
        for i in range(5, len(closes)):
            if closes[i]["day"] >= since:
                out[name][closes[i]["day"]] = (closes[i]["close"] / closes[i - 5]["close"] - 1) * 100

    # Count indicators for the current day are partial. Estimate the trailing 24h instead so the
    # index does not collapse at midnight: yesterday's count scaled by the part of today not yet elapsed, plus today so far.
    now = datetime.now(LOCAL_TZ)
    elapsed = (now.hour * 60 + now.minute) / 1440
    today_s, yesterday_s = labels[-1], labels[-2]
    for k, s in out.items():
        if k in PARTIAL_DAY_EXEMPT or yesterday_s not in s:
            continue
        s[today_s] = round(s[yesterday_s] * (1 - elapsed) + s.get(today_s, 0), 2)

    return {k: v for k, v in out.items() if v}
