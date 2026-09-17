"""Taiwan Coast Guard Administration press releases (China Coast Guard activity)."""
from datetime import datetime

from selectolax.parser import HTMLParser

from app import http, items
from app.config import LOCAL_TZ

BASE = "https://www.cga.gov.tw/GipOpen/wSite/"


def run() -> str:
    tree = HTMLParser(http.get(BASE + "lp?ctNode=650&mp=999").text)
    new = 0
    for a in tree.css("a.news-list"):
        title, date_node = a.attributes.get("title", "").strip(), a.css_first(".date")
        if not title or not date_node:
            continue
        roc_year, month, day = (int(x) for x in date_node.text().strip().split("/"))
        # Date only; Taiwan and Singapore share UTC+8
        published = datetime(roc_year + 1911, month, day, tzinfo=LOCAL_TZ)
        new += items.save("Taiwan Coast Guard", BASE + a.attributes["href"], title, published,
                          context_implied=True, dedup_key=a.attributes["href"])
    return f"{new} new releases"
