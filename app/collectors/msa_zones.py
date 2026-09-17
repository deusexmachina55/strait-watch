"""Parse MSA warning detail pages into closure polygons (Chinese-language entries only; English twins are dead links)."""
import json
import re
import time
from contextlib import closing
from datetime import date, datetime, timezone

from app import db, geo, http
from app.web.data import rows

PER_RUN = 60
# 25°26′9″N 119°20′43″E | 29-27.00N 122-02.50E | 23-05-57N116-32-49E | 22-03.40N 113-39.10E
COORD = re.compile(
    r"(\d{1,2})[°\-](\d{1,2}(?:\.\d+)?)(?:[′'\-](\d{1,2}(?:\.\d+)?))?[′'″\"]*\s*([NS])[^0-9A-Za-z]{0,8}"
    r"(\d{1,3})[°\-](\d{1,2}(?:\.\d+)?)(?:[′'\-](\d{1,2}(?:\.\d+)?))?[′'″\"]*\s*([EW])")
KINDS = [
    ("rocket", re.compile(r"火箭|运载|残骸|ROCKET|WRECKAGE|DEBRIS", re.I)),
    ("live_fire", re.compile(r"实弹|射击|GUN FIRING|FIRING|MISSILE", re.I)),
    ("exercise", re.compile(r"演习|演练|军事活动|军事训练|军事任务|军事|MILITARY|EXERCISE", re.I)),
]


def dms(deg: str, minutes: str, seconds: str | None) -> float:
    return int(deg) + float(minutes) / 60 + (float(seconds) / 60 / 60 if seconds else 0)


def parse(text: str, issued: date) -> dict:
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text))
    i = text.find("发布时间")
    body = text[i:i + 3000] if i > 0 else text
    points = [(dms(a, b, c) * (1 if d == "N" else -1), dms(e, f, g) * (1 if h == "E" else -1))
              for a, b, c, d, e, f, g, h in COORD.findall(body)]
    kind = next((k for k, p in KINDS if p.search(body)), "other")
    area_m = re.search(r"航警\d+/\d+[，,]\s*([^，,。]{1,20})[，,]", body)
    sea_area = area_m.group(1).strip() if area_m else None
    # Active window: "9月18日0730时至2300时", "9月18日至19日", "自8月26日0100时至1800时"
    starts = ends = None
    m = re.search(r"(\d{1,2})月(\d{1,2})日", body)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        year = issued.year + (1 if month < issued.month - 6 else 0)
        starts = date(year, month, day)
        m2 = re.search(r"至\s*(?:(\d{1,2})月)?(\d{1,2})日", body[m.end():m.end() + 40])
        ends = date(year, int(m2.group(1) or month), int(m2.group(2))) if m2 else starts
        if ends < starts:
            ends = starts
    window = re.search(r"((?:自)?\d{1,2}月\d{1,2}日[^，,。在]{0,30}(?:至|到)[^，,。在]{0,30})", body)
    return {"points": points, "kind": kind, "sea_area": sea_area, "starts": starts.isoformat() if starts else None,
            "ends": ends.isoformat() if ends else None, "window": window.group(1) if window else None}


def run() -> str:
    todo = rows("SELECT w.url, w.region, w.number, w.title, w.issued_date FROM msa_warnings w LEFT JOIN msa_zones z ON z.url = w.url "
                "WHERE w.military = 1 AND w.lang = 'zh' AND z.url IS NULL ORDER BY w.issued_date DESC LIMIT ?", PER_RUN)
    if not todo:
        return "no new warnings"
    parsed = failed = 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for w in todo:
        try:
            info = parse(http.get(w["url"]).text, date.fromisoformat(w["issued_date"]))
        except Exception as e:
            info = {"points": [], "kind": "other", "sea_area": None, "starts": None, "ends": None, "window": repr(e)[:100]}
        pts = info["points"]
        ok = len(pts) >= 3
        lat, lon = geo.centroid(pts) if pts else (None, None)
        with closing(db.connect()) as conn, conn:
            conn.execute(
                "INSERT OR REPLACE INTO msa_zones (url, region, number, kind, sea_area, starts, ends, window, polygon, lat, lon, area_km2, ok, parsed_utc) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (w["url"], w["region"], w["number"], info["kind"], info["sea_area"], info["starts"] or w["issued_date"],
                 info["ends"] or info["starts"] or w["issued_date"], info["window"], json.dumps(pts), lat, lon,
                 geo.polygon_area_km2(pts) if ok else 0, ok, now))
        parsed += ok
        failed += not ok
        time.sleep(1)
    return f"{parsed} zones parsed, {failed} without coordinates"
