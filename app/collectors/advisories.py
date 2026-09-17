"""US State Department travel advisories for Taiwan and China."""
import calendar
import re
from contextlib import closing
from datetime import datetime, timezone

import feedparser

from app import db, http

URL = "https://travel.state.gov/_res/rss/TAsTWs.xml"
COUNTRIES = {"TW": "Taiwan", "CH": "China"}


def run() -> str:
    feed = feedparser.parse(http.get(URL).content)
    new = 0
    with closing(db.connect()) as conn, conn:
        for e in feed.entries:
            terms = [t.get("term", "") for t in e.get("tags", [])]
            code = next((t for t in terms if t in COUNTRIES), None)
            level = re.search(r"Level (\d)", e.title)
            if not code or not level or not e.get("published_parsed"):
                continue
            published = datetime.fromtimestamp(calendar.timegm(e.published_parsed), timezone.utc)
            cur = conn.execute(
                "INSERT OR IGNORE INTO advisories (title, country, level, published_utc, url) VALUES (?, ?, ?, ?, ?)",
                (e.title, COUNTRIES[code], int(level.group(1)), published.isoformat(timespec="seconds"), e.link))
            new += cur.rowcount
    return f"{new} new advisories"
