"""China MSA navigation warnings, per regional bureau channel on www.msa.gov.cn."""
import re
from contextlib import closing
from datetime import date, datetime, timedelta, timezone

from selectolax.parser import HTMLParser

from app import db, http
from app.config import SETTINGS
from app.db import CJK

BASE = "https://www.msa.gov.cn"
MILITARY = re.compile("|".join(re.escape(t) for t in SETTINGS["msa"]["military_terms"]), re.IGNORECASE)
MAX_PAGES = 40


def page(channel_id: str, n: int) -> list[dict]:
    suffix = "index.jhtml" if n == 1 else f"index_{n}.jhtml"
    tree = HTMLParser(http.get(f"{BASE}/{channel_id}/{suffix}").text)
    rows = []
    for li in tree.css("li.main_list_li"):
        a, name, when = li.css_first("a"), li.css_first(".name"), li.css_first(".time")
        if not (a and name and when):
            continue
        title = name.text().strip()
        number = re.search(r"[一-鿿]航警\d+/\d+|[A-Z]{2}\d+/\d+", title)
        rows.append({
            "url": BASE + a.attributes["href"].split("?")[0],
            "title": title,
            "number": number.group(0) if number else None,
            "issued_date": when.text().strip(),
            "military": int(bool(MILITARY.search(title))),
            "lang": "zh" if CJK.search(title) else "en",
        })
    return rows


def run() -> str:
    cutoff = (date.today() - timedelta(days=SETTINGS["msa"]["backfill_days"])).isoformat()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    summary = []
    for channel in SETTINGS["msa"]["channels"]:
        with closing(db.connect()) as conn:
            has_history = conn.execute(
                "SELECT 1 FROM msa_warnings WHERE region = ? AND issued_date <= ? LIMIT 1", (channel["region"], cutoff)).fetchone()
        new = 0
        # First run walks back to the cutoff; later runs stop at the first page with nothing new
        for n in range(1, MAX_PAGES + 1):
            rows = page(channel["id"], n)
            page_new = 0
            with closing(db.connect()) as conn, conn:
                for row in rows:
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO msa_warnings (url, region, number, title, issued_date, military, lang, fetched_utc) "
                        "VALUES (:url, :region, :number, :title, :issued_date, :military, :lang, :fetched)",
                        row | {"region": channel["region"], "fetched": now})
                    page_new += cur.rowcount
            new += page_new
            if not rows or rows[-1]["issued_date"] <= cutoff or (has_history and page_new == 0):
                break
        summary.append(f"{channel['region']} +{new}")
    return ", ".join(summary)
