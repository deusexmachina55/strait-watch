"""Japan Joint Staff press releases on Chinese military movements."""
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from selectolax.parser import HTMLParser

from app import http, items

BASE = "https://www.mod.go.jp"
TOKYO = ZoneInfo("Asia/Tokyo")


def run() -> str:
    tree = HTMLParser(http.get(BASE + "/js/press/index.html").text)
    new = 0
    for a in tree.css("li > a"):
        title_node, time_node = a.css_first("h5"), a.css_first("time")
        if not (title_node and time_node) or "中国" not in title_node.text():
            continue
        title = title_node.text().strip()
        # datetime attribute is not zero-padded (e.g. 2024-11-1) and occasionally malformed
        m_date = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", time_node.attributes.get("datetime", ""))
        if not m_date:
            continue
        y, m, d = (int(x) for x in m_date.groups())
        day = f"{y:04d}-{m:02d}-{d:02d}"
        # Releases carry a date only; titles repeat (e.g. the same aircraft type), so dedup on date + title
        published = datetime(y, m, d, tzinfo=TOKYO)
        new += items.save("Japan MOD", BASE + a.attributes["href"], title, published,
                          context_implied=True, dedup_key=f"{day} {title}")
    return f"{new} new notices"
