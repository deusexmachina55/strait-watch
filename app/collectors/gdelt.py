"""GDELT 2.0 event export files (15-minute CSV zips, no API rate limit)."""
import csv
import io
import zipfile
from contextlib import closing
from datetime import datetime, timedelta, timezone

from app import db, http
from app.config import SETTINGS

BASE = "http://data.gdeltproject.org/gdeltv2/"
DYADS = {frozenset(d.split("-")): d for d in SETTINGS["gdelt"]["dyads"]}
BACKFILL_FILES_PER_RUN = 60

# Export column positions (GDELT 2.0 event codebook)
ACTOR1_COUNTRY, ACTOR2_COUNTRY, ROOT_CODE, QUAD_CLASS, GOLDSTEIN, AVG_TONE = 7, 17, 28, 29, 30, 34


def aggregate(rows) -> dict:
    totals = {}
    for row in rows:
        dyad = DYADS.get(frozenset((row[ACTOR1_COUNTRY], row[ACTOR2_COUNTRY])))
        if not dyad:
            continue
        t = totals.setdefault(dyad, dict(events=0, verbal_conflict=0, material_conflict=0, force_posture=0, coerce=0,
                                          fight=0, goldstein_sum=0.0, tone_sum=0.0))
        root, quad = row[ROOT_CODE], row[QUAD_CLASS]
        t["events"] += 1
        t["verbal_conflict"] += quad == "3"
        t["material_conflict"] += quad == "4"
        t["force_posture"] += root == "15"
        t["coerce"] += root == "17"
        t["fight"] += root in ("18", "19", "20")
        t["goldstein_sum"] += float(row[GOLDSTEIN] or 0)
        t["tone_sum"] += float(row[AVG_TONE] or 0)
    return totals


def process(stamp: str) -> str:
    """Download and aggregate one 15-minute file. stamp is YYYYMMDDHHMMSS (UTC)."""
    name = f"{stamp}.export.CSV.zip"
    r = http.client.get(BASE + name)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if r.status_code == 404:
        with closing(db.connect()) as conn, conn:
            conn.execute("INSERT OR REPLACE INTO gdelt_files VALUES (?, 'missing', ?)", (name, now))
        return "missing"
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        text = z.read(z.namelist()[0]).decode("utf-8", errors="replace")
    totals = aggregate(csv.reader(io.StringIO(text), delimiter="\t"))
    day = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}"
    with closing(db.connect()) as conn, conn:
        for dyad, t in totals.items():
            conn.execute(
                "INSERT INTO gdelt_daily (day, dyad) VALUES (?, ?) ON CONFLICT DO NOTHING", (day, dyad))
            conn.execute(
                "UPDATE gdelt_daily SET events = events + :events, verbal_conflict = verbal_conflict + :verbal_conflict, "
                "material_conflict = material_conflict + :material_conflict, force_posture = force_posture + :force_posture, "
                "coerce = coerce + :coerce, fight = fight + :fight, goldstein_sum = goldstein_sum + :goldstein_sum, "
                "tone_sum = tone_sum + :tone_sum WHERE day = :day AND dyad = :dyad",
                t | {"day": day, "dyad": dyad},
            )
        conn.execute("INSERT INTO gdelt_files VALUES (?, 'ok', ?)", (name, now))
    return "ok"


def processed() -> set[str]:
    with closing(db.connect()) as conn:
        return {r[0][:14] for r in conn.execute("SELECT name FROM gdelt_files")}


def run() -> str:
    """Process the latest file plus any gaps from the last 6 hours."""
    latest = http.get(BASE + "lastupdate.txt").text.split()[2].rsplit("/", 1)[-1][:14]
    end = datetime.strptime(latest, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    done = processed()
    stamps = [(end - timedelta(minutes=15 * i)).strftime("%Y%m%d%H%M%S") for i in range(24)]
    results = [process(s) for s in stamps if s not in done]
    return f"processed {len(results)} files, latest {latest}"


def backfill() -> str:
    """Work backwards through history, a batch per run, until backfill_days is covered."""
    done = processed()
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    # Start before the 6-hour window that run() owns, so both jobs never process the same file
    end = now - timedelta(hours=6, minutes=now.minute % 15 + 15)
    todo = []
    for i in range(SETTINGS["gdelt"]["backfill_days"] * 96):
        stamp = (end - timedelta(minutes=15 * i)).strftime("%Y%m%d%H%M%S")
        if stamp not in done:
            todo.append(stamp)
            if len(todo) == BACKFILL_FILES_PER_RUN:
                break
    for stamp in todo:
        process(stamp)
    return f"backfilled {len(todo)} files" if todo else "backfill complete"
