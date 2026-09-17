"""Tripwires: keyword hits on fresh items, indicator spikes, index levels. Each fires a Telegram alert with a cooldown."""
import json
import logging
import re
from contextlib import closing
from datetime import date, datetime, timedelta, timezone

from app import db
from app.alerts import telegram
from app.config import SETTINGS
from app.items import _pattern
from app.web.data import rows

log = logging.getLogger("uvicorn.error")
CFG = SETTINGS["tripwires"]
KEYWORDS = [(t, _pattern(t["terms"])) for t in CFG["keywords"]]
FRESH_HOURS = 48


def recently_fired(conn, name: str, hours: float) -> bool:
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    return conn.execute("SELECT 1 FROM tripwire_log WHERE tripwire = ? AND fired_utc >= ? LIMIT 1", (name, since)).fetchone() is not None


def fire(conn, name: str, severity: int, detail: str, url: str | None, index_line: str) -> None:
    text = f"Strait Watch tripwire: {name} (severity {severity}/5)\n{detail}\n{url or ''}\n{index_line}".strip()
    try:
        telegram.send(text)
        alerted = 1
    except Exception as e:
        log.error("tripwire alert failed: %r", e)
        alerted = 0
    conn.execute("INSERT INTO tripwire_log (tripwire, fired_utc, severity, detail, url, alerted) VALUES (?, ?, ?, ?, ?, ?)",
                 (name, datetime.now(timezone.utc).isoformat(timespec="seconds"), severity, detail, url, alerted))


def evaluate() -> str:
    today = date.today().isoformat()
    score = rows("SELECT * FROM scores WHERE day = ?", today)
    score = score[0] if score else None
    details = json.loads(score["details"]) if score else {}
    index_line = (f"Index {score['composite']:.0f} (military {score['military']:.0f}, economic {score['economic']:.0f}, "
                  f"diplomatic {score['diplomatic']:.0f}, rhetoric {score['rhetoric']:.0f})") if score else "Index not computed yet"
    fresh_since = (datetime.now(timezone.utc) - timedelta(hours=FRESH_HOURS)).isoformat(timespec="seconds")
    items = rows("SELECT source, title, snippet, url, published_utc FROM items WHERE relevant = 1 AND published_utc >= ? "
                 "ORDER BY published_utc DESC", fresh_since)
    fired = []
    with closing(db.connect()) as conn, conn:
        for tw, pattern in KEYWORDS:
            if recently_fired(conn, tw["name"], tw["cooldown"]):
                continue
            hit = next((it for it in items if pattern.search(f"{it['title']} {it['snippet'] or ''}")), None)
            if hit:
                fire(conn, tw["name"], tw["severity"], f"{hit['source']}: {hit['title']}", hit["url"], index_line)
                fired.append(tw["name"])

        for tw in CFG["indicators"]:
            d = details.get(tw["indicator"])
            if not d or recently_fired(conn, tw["name"], tw["cooldown"]):
                continue
            if (tw["sigma"] and d["z"] >= tw["sigma"]) or d["value"] >= tw["absolute"]:
                fire(conn, tw["name"], tw["severity"], f"{tw['indicator']} = {d['value']} (z {d['z']:+.1f})", None, index_line)
                fired.append(tw["name"])

        # Combination: PLA spike together with a named exercise in the last 48h
        pla = details.get("pla_aircraft")
        if pla and pla["z"] >= 2 and any(p.search(it["title"]) for t, p in KEYWORDS if t["name"] == "named_exercise" for it in items):
            if not recently_fired(conn, "pla_spike_named_exercise", 24):
                fire(conn, "pla_spike_named_exercise", 5, f"PLA aircraft {pla['value']} (z {pla['z']:+.1f}) during a named exercise", None, index_line)
                fired.append("pla_spike_named_exercise")

        if score:
            for level in CFG["index_levels"]:
                name = f"index_{level}"
                if score["composite"] >= level and not recently_fired(conn, name, 24):
                    fire(conn, name, 3 if level < 75 else (4 if level < 90 else 5), f"Composite index crossed {level}", None, index_line)
                    fired.append(name)
    return f"fired {', '.join(fired)}" if fired else "none"
