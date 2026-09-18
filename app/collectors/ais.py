"""AIS positions from aisstream.io (free websocket). Runs as a background thread with reconnects.

Keeps the latest position of every vessel in the box, a 10-minute track for flagged classes
(coast guard, military, tanker, law enforcement) and per-day sightings in named areas."""
import asyncio
import json
import logging
import os
import re
import threading
from contextlib import closing
from datetime import date, datetime, timedelta, timezone

import websockets

from app import db, geo, settings
from app.config import LOCAL_TZ

log = logging.getLogger("uvicorn.error")
URL = "wss://stream.aisstream.io/v0/stream"
BOX = [[[20.5, 115.5], [27.5, 123.5]]]  # [[lat, lon] sw, [lat, lon] ne]
FLAGGED = {"coast_guard", "military", "tanker", "law"}
CCG = re.compile(r"HAI ?JING|CCG|COAST ?GUARD|海警|CHINA ?COAST|ZHONG ?GUO ?HAI ?JING", re.I)
TRACK_SECONDS = 30      # closest spacing of stored track points, per vessel
TRACK_HOURS = 12        # how long track points are kept (trails on the map)
RETENTION_DAYS = 7      # how long the latest position of a vessel is kept


def classify(name: str | None, ship_type: int | None, mmsi: int) -> str:
    name = (name or "").upper()
    chinese = str(mmsi)[:3] in ("412", "413", "414")
    if CCG.search(name) or (ship_type == 55 and chinese):
        return "coast_guard"
    if ship_type == 35:
        return "military"
    if ship_type and 80 <= ship_type <= 89:
        return "tanker"
    if ship_type == 55:
        return "law"
    if ship_type and 70 <= ship_type <= 79:
        return "cargo"
    if ship_type == 30:
        return "fishing"
    return "other"


class Collector:
    def __init__(self):
        self.pending = {}   # mmsi -> latest position dict
        self.static = {}    # mmsi -> (name, type)
        self.last_track = {}
        self.messages = 0

    def handle(self, msg: dict) -> None:
        meta, kind = msg.get("MetaData", {}), msg.get("MessageType")
        mmsi = meta.get("MMSI")
        if not mmsi:
            return
        self.messages += 1
        if kind == "ShipStaticData":
            s = msg["Message"]["ShipStaticData"]
            self.static[mmsi] = ((s.get("Name") or meta.get("ShipName") or "").strip(), s.get("Type"))
        elif kind == "PositionReport":
            p = msg["Message"]["PositionReport"]
            self.pending[mmsi] = {"lat": p["Latitude"], "lon": p["Longitude"], "sog": p.get("Sog"), "cog": p.get("Cog"),
                                  "name": (meta.get("ShipName") or "").strip(), "ts": datetime.now(timezone.utc)}

    def flush(self) -> None:
        if not self.pending:
            return
        batch, self.pending = self.pending, {}
        today = datetime.now(LOCAL_TZ).date().isoformat()
        with closing(db.connect()) as conn, conn:
            for mmsi, p in batch.items():
                name, ship_type = self.static.get(mmsi, (p["name"], None))
                known = conn.execute("SELECT name, ship_type FROM ais_vessels WHERE mmsi = ?", (mmsi,)).fetchone()
                if known:
                    name = name or known["name"]
                    ship_type = ship_type or known["ship_type"]
                cls = classify(name, ship_type, mmsi)
                ts = p["ts"].isoformat(timespec="seconds")
                conn.execute(
                    "INSERT INTO ais_vessels (mmsi, name, ship_type, cls, lat, lon, sog, cog, ts_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(mmsi) DO UPDATE SET name = excluded.name, ship_type = excluded.ship_type, cls = excluded.cls, "
                    "lat = excluded.lat, lon = excluded.lon, sog = excluded.sog, cog = excluded.cog, ts_utc = excluded.ts_utc",
                    (mmsi, name, ship_type, cls, p["lat"], p["lon"], p["sog"], p["cog"], ts))
                last = self.last_track.get(mmsi)
                if not last or p["ts"] - last >= timedelta(seconds=TRACK_SECONDS):
                    conn.execute("INSERT OR IGNORE INTO ais_tracks (mmsi, ts_utc, lat, lon, sog, cog) VALUES (?, ?, ?, ?, ?, ?)",
                                 (mmsi, ts, p["lat"], p["lon"], p["sog"], p["cog"]))
                    self.last_track[mmsi] = p["ts"]
                if cls in FLAGGED:
                    for zone in geo.zones_for(p["lat"], p["lon"]):
                        conn.execute("INSERT OR IGNORE INTO ais_sightings (day, mmsi, zone, cls) VALUES (?, ?, ?, ?)", (today, mmsi, zone, cls))

    async def stream(self) -> None:
        async with websockets.connect(URL, open_timeout=30, ping_interval=20) as ws:
            await ws.send(json.dumps({"APIKey": settings.get("aisstream_api_key"), "BoundingBoxes": BOX,
                                      "FilterMessageTypes": ["PositionReport", "ShipStaticData"]}))
            last_flush = last_status = datetime.now(timezone.utc)
            while True:
                try:
                    self.handle(json.loads(await asyncio.wait_for(ws.recv(), 60)))
                except asyncio.TimeoutError:
                    pass
                now = datetime.now(timezone.utc)
                if now - last_flush >= timedelta(seconds=10):
                    self.flush()
                    last_flush = now
                if now - last_status >= timedelta(seconds=60):
                    self.status(now)
                    last_status = now

    def status(self, now: datetime) -> None:
        from app.scheduler import record
        with closing(db.connect()) as conn, conn:
            n = conn.execute("SELECT COUNT(*) FROM ais_vessels WHERE ts_utc >= ?",
                             ((now - timedelta(minutes=30)).isoformat(timespec="seconds"),)).fetchone()[0]
            conn.execute("DELETE FROM ais_tracks WHERE ts_utc < ?", ((now - timedelta(hours=TRACK_HOURS)).isoformat(timespec="seconds"),))
            conn.execute("DELETE FROM ais_vessels WHERE ts_utc < ?", ((now - timedelta(days=RETENTION_DAYS)).isoformat(timespec="seconds"),))
        self.last_track = {m: t for m, t in self.last_track.items() if now - t < timedelta(hours=1)}
        record("ais", True, f"{self.messages} messages/min, {n} vessels seen in 30 min")
        self.messages = 0

    async def forever(self) -> None:
        from app.scheduler import record
        while True:
            try:
                await self.stream()
            except Exception as e:
                log.error("ais stream failed: %r", e)
                record("ais", False, repr(e)[:300])
                await asyncio.sleep(30)


_thread: threading.Thread | None = None


def start() -> str:
    """Start the stream thread if a key exists and it is not already running. Safe to call repeatedly."""
    global _thread
    from app.scheduler import record
    if _thread and _thread.is_alive():
        return "running"
    if not settings.get("aisstream_api_key"):
        record("ais", False, "aisstream key not set (Settings page)")
        return "no key"
    _thread = threading.Thread(target=lambda: asyncio.run(Collector().forever()), name="ais", daemon=True)
    _thread.start()
    return "started"


def probe(key: str) -> int:
    """Open the stream for 10 seconds with the given key and count messages. Raises if the key is rejected."""
    async def run() -> int:
        async with websockets.connect(URL, open_timeout=30) as ws:
            await ws.send(json.dumps({"APIKey": key, "BoundingBoxes": BOX, "FilterMessageTypes": ["PositionReport"]}))
            n, deadline = 0, asyncio.get_event_loop().time() + 10
            while asyncio.get_event_loop().time() < deadline:
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), 3))
                except asyncio.TimeoutError:
                    continue
                if msg.get("error") or msg.get("MessageType") == "error":
                    raise RuntimeError(str(msg)[:200])
                n += 1
            return n
    return asyncio.run(run())
