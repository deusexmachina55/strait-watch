"""RSS and Google News feeds."""
import calendar
import re
from datetime import datetime, timezone

import feedparser

from app import http, items
from app.config import SETTINGS


def run() -> str:
    new = 0
    failed = []
    for feed in SETTINGS["rss"]:
        try:
            parsed = feedparser.parse(http.get(feed["url"]).content)
        except Exception as e:
            failed.append(f"{feed['name']}: {e!r}")
            continue
        google = "news.google.com" in feed["url"]
        for entry in parsed.entries:
            stamp = entry.get("published_parsed") or entry.get("updated_parsed")
            if not stamp or not entry.get("title"):
                continue
            title = entry.title
            if google:
                # Google News appends " - Publisher" to titles
                title = re.sub(r"\s+-\s+[^-]+$", "", title)
            published = datetime.fromtimestamp(calendar.timegm(stamp), timezone.utc)
            snippet = "" if google else items.clean_text(entry.get("summary", ""))
            new += items.save(feed["name"], entry.link, title, published, snippet, feed.get("context", False))
    # One broken feed should not hide the others, but still flags the job
    if failed:
        raise RuntimeError(f"{new} new items; failed feeds: " + "; ".join(failed))
    return f"{new} new items"
