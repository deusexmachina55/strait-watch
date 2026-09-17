"""Polymarket probabilities for China/Taiwan escalation markets (public Gamma API)."""
import json
from contextlib import closing
from datetime import datetime, timezone

from app import db, http
from app.config import SETTINGS

URL = "https://gamma-api.polymarket.com/public-search"


def run() -> str:
    cfg = SETTINGS["polymarket"]
    events = http.get(URL, params={"q": cfg["search"]}).json().get("events", [])
    ts = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0).isoformat()
    saved = 0
    with closing(db.connect()) as conn, conn:
        for event in events:
            title = event.get("title", "").lower()
            if event.get("closed") or not all(w in title for w in cfg["title_must_contain"]):
                continue
            for m in event.get("markets", []):
                if m.get("closed") or not m.get("outcomePrices"):
                    continue
                outcomes, prices = json.loads(m["outcomes"]), json.loads(m["outcomePrices"])
                if "Yes" not in outcomes:
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO market_odds (market_id, question, ts_utc, probability, volume) VALUES (?, ?, ?, ?, ?)",
                    (m["id"], m["question"], ts, float(prices[outcomes.index("Yes")]), float(m.get("volume") or 0)))
                saved += 1
    return f"{saved} markets"
