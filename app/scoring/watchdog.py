"""Telegram alert when a job has not succeeded within its allowed age. One alert per job per cooldown."""
import logging
from contextlib import closing
from datetime import datetime, timedelta, timezone

from app import db
from app.alerts import telegram
from app.config import SETTINGS
from app.web.data import rows

log = logging.getLogger("uvicorn.error")
CFG = SETTINGS["watchdog"]


def run() -> str:
    now = datetime.now(timezone.utc)
    status = {r["job"]: r for r in rows("SELECT job, last_run_utc, last_ok_utc, ok, message FROM job_status")}
    stale = []
    with closing(db.connect()) as conn, conn:
        for job, max_hours in CFG["max_age_hours"].items():
            s = status.get(job)
            last_ok = datetime.fromisoformat(s["last_ok_utc"]) if s and s["last_ok_utc"] else None
            if last_ok and now - last_ok <= timedelta(hours=max_hours):
                continue
            name = f"watchdog_{job}"
            since = (now - timedelta(hours=CFG["cooldown_hours"])).isoformat(timespec="seconds")
            if conn.execute("SELECT 1 FROM tripwire_log WHERE tripwire = ? AND fired_utc >= ?", (name, since)).fetchone():
                continue
            detail = (f"{job}: last success {last_ok.astimezone().strftime('%d %b %H:%M') if last_ok else 'never'}, "
                      f"last message: {(s or {}).get('message') or 'no run recorded'}")[:300]
            try:
                telegram.send(f"Strait Watch watchdog: {detail}")
                alerted = 1
            except Exception as e:
                log.error("watchdog alert failed: %r", e)
                alerted = 0
            conn.execute("INSERT INTO tripwire_log (tripwire, fired_utc, severity, detail, url, alerted) VALUES (?, ?, 2, ?, NULL, ?)",
                         (name, now.isoformat(timespec="seconds"), detail, alerted))
            stale.append(job)
    return f"alerted {', '.join(stale)}" if stale else "all jobs healthy"
