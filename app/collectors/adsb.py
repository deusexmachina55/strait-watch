"""ADS-B via adsb.lol (free, no key): civil traffic count over the Strait and military aircraft in the region."""
from contextlib import closing
from datetime import datetime, timedelta, timezone

from app import db, geo, http
from app.config import LOCAL_TZ

POINT = "https://api.adsb.lol/v2/point/24.0/120.5/250"
MIL = "https://api.adsb.lol/v2/mil"
BOX = (18.0, 30.0, 112.0, 128.0)
TRACK_MINUTES = 5
RETENTION_DAYS = 7


def upsert(conn, a: dict, military: bool, ts: str) -> None:
    conn.execute(
        "INSERT INTO adsb_aircraft (hex, flight, type, reg, desc, military, lat, lon, alt, gs, track, ts_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(hex) DO UPDATE SET flight = excluded.flight, type = excluded.type, reg = excluded.reg, desc = excluded.desc, "
        "military = excluded.military, lat = excluded.lat, lon = excluded.lon, alt = excluded.alt, gs = excluded.gs, track = excluded.track, ts_utc = excluded.ts_utc",
        (a["hex"], (a.get("flight") or "").strip(), a.get("t"), a.get("r"), a.get("desc"), military, a["lat"], a["lon"],
         a.get("alt_baro") if isinstance(a.get("alt_baro"), (int, float)) else None, a.get("gs"), a.get("track"), ts))


def run() -> str:
    now = datetime.now(timezone.utc)
    ts = now.isoformat(timespec="seconds")
    today = now.astimezone(LOCAL_TZ).date().isoformat()
    responses = [http.client.get(POINT), http.client.get(MIL)]
    if any(r.status_code == 429 for r in responses):
        return "rate limited by adsb.lol, skipped this poll"
    for r in responses:
        r.raise_for_status()
    civil = [a for a in responses[0].json().get("ac", []) if a.get("lat") and not ((a.get("dbFlags") or 0) & 1)]
    mil = [a for a in responses[1].json().get("ac", []) if a.get("lat") and geo.in_box(a["lat"], a["lon"], BOX)]
    with closing(db.connect()) as conn, conn:
        for a in civil:
            upsert(conn, a, False, ts)
        for a in mil:
            upsert(conn, a, True, ts)
            last = conn.execute("SELECT MAX(ts_utc) FROM adsb_tracks WHERE hex = ?", (a["hex"],)).fetchone()[0]
            if not last or now - datetime.fromisoformat(last) >= timedelta(minutes=TRACK_MINUTES):
                conn.execute("INSERT OR IGNORE INTO adsb_tracks (hex, ts_utc, lat, lon, alt) VALUES (?, ?, ?, ?, ?)",
                             (a["hex"], ts, a["lat"], a["lon"], a.get("alt_baro") if isinstance(a.get("alt_baro"), (int, float)) else None))
            conn.execute("INSERT OR IGNORE INTO adsb_sightings (day, hex, kind) VALUES (?, ?, 'military')", (today, a["hex"]))
        conn.execute("INSERT OR REPLACE INTO adsb_counts (ts_utc, civil, military) VALUES (?, ?, ?)", (ts, len(civil), len(mil)))
        cutoff = (now - timedelta(days=RETENTION_DAYS)).isoformat(timespec="seconds")
        conn.execute("DELETE FROM adsb_tracks WHERE ts_utc < ?", (cutoff,))
        conn.execute("DELETE FROM adsb_aircraft WHERE ts_utc < ?", (cutoff,))
        conn.execute("DELETE FROM adsb_counts WHERE ts_utc < ?", ((now - timedelta(days=90)).isoformat(timespec="seconds"),))
    return f"{len(civil)} civil over the Strait, {len(mil)} military in region"
