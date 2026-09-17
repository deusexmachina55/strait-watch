"""Taiwan MND daily PLA activity report."""
import re
from contextlib import closing
from datetime import datetime, timezone

from selectolax.parser import HTMLParser

from app import db, http

BASE = "https://www.mnd.gov.tw/"


def count(pattern: str, text: str) -> int:
    m = re.search(pattern, text)
    return int(m.group(1)) if m else 0


def parse_report(text: str) -> dict:
    text = re.sub(r"\s+", "", text)
    activity = text.split("活動動態", 1)[-1]
    return {
        "aircraft": count(r"共機(\d+)架次", activity),
        # Parenthetical count: aircraft that entered Taiwan's northern/central/southwestern/eastern airspace
        "aircraft_entered": count(r"共機\d+架次（[^）]*?(\d+)架次）", activity),
        "median_line_crossed": int("逾越中線" in activity),
        "navy_ships": count(r"共艦(\d+)艘", activity),
        "official_ships": count(r"公務船(\d+)艘", activity),
        "balloons": count(r"氣球(\d+)", activity),
    }


def run() -> str:
    tree = HTMLParser(http.get(BASE + "news/plaactlist").text)
    with closing(db.connect()) as conn:
        known = {r[0] for r in conn.execute("SELECT report_date FROM pla_daily")}
    added = []
    for link in tree.css("a.news_list"):
        href = link.attributes.get("href", "")
        date_node = link.css_first(".date")
        if "plaact/" not in href or not date_node:
            continue
        roc_year, month, day = (int(x) for x in date_node.text().strip().split("."))
        report_date = f"{roc_year + 1911:04d}-{month:02d}-{day:02d}"
        if report_date in known:
            continue
        url = BASE + href.lstrip("/")
        values = parse_report(HTMLParser(http.get(url).text).body.text())
        with closing(db.connect()) as conn, conn:
            conn.execute(
                "INSERT OR REPLACE INTO pla_daily (report_date, aircraft, aircraft_entered, median_line_crossed, navy_ships, "
                "official_ships, balloons, url, fetched_utc) VALUES (:report_date, :aircraft, :aircraft_entered, "
                ":median_line_crossed, :navy_ships, :official_ships, :balloons, :url, :fetched)",
                values | {"report_date": report_date, "url": url, "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds")},
            )
        added.append(report_date)
    return f"added {', '.join(sorted(added))}" if added else "no new reports"
