import logging
from contextlib import closing
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app import backup, db, scoring
from app.collectors import adsb, advisories, ais, coast_guard, gdelt, japan_mod, msa, msa_zones, mnd, polymarket, prices, rss
from app.scoring import watchdog
from app.config import LOCAL_TZ, SETTINGS
from app.llm import analyze, digest

LLM = SETTINGS["llm"]

log = logging.getLogger("uvicorn.error")
scheduler = AsyncIOScheduler(timezone=timezone.utc)


def record(job: str, ok: bool, message: str | None = None) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(db.connect()) as conn, conn:
        conn.execute(
            "INSERT INTO job_status (job, last_run_utc, last_ok_utc, ok, message) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(job) DO UPDATE SET last_run_utc = excluded.last_run_utc, ok = excluded.ok, message = excluded.message, "
            "last_ok_utc = CASE WHEN excluded.ok THEN excluded.last_run_utc ELSE job_status.last_ok_utc END",
            (job, now, now if ok else None, ok, message),
        )
        if not ok:
            conn.execute(
                "INSERT INTO job_failures (job, ran_at_utc, message) VALUES (?, ?, ?)",
                (job, now, message),
            )


def add_job(name: str, fn, trigger="interval", run_now: bool = True, **trigger_args) -> None:
    def run():
        try:
            message = fn()
        except Exception as e:
            log.error("job %s failed: %r", name, e)
            record(name, False, repr(e)[:500])
            return
        record(name, True, message)

    extra = {"next_run_time": datetime.now(timezone.utc)} if run_now else {}
    scheduler.add_job(run, trigger, id=name, replace_existing=True, max_instances=1, coalesce=True,
                      misfire_grace_time=300, **extra, **trigger_args)


def heartbeat() -> None:
    pass


def start() -> None:
    add_job("heartbeat", heartbeat, seconds=60)
    # MND publishes once a day, usually mid-morning Taiwan time; poll hourly in the window
    add_job("mnd", mnd.run, CronTrigger(hour="8-18", minute=5, timezone=LOCAL_TZ))
    add_job("rss", rss.run, minutes=15)
    add_job("gdelt", gdelt.run, minutes=15)
    add_job("gdelt_backfill", gdelt.backfill, minutes=2)
    add_job("prices", prices.run, minutes=5)
    add_job("msa", msa.run, hours=1)
    add_job("japan_mod", japan_mod.run, hours=3)
    add_job("coast_guard", coast_guard.run, hours=3)
    add_job("advisories", advisories.run, hours=6)
    add_job("polymarket", polymarket.run, hours=1)
    add_job("msa_zones", msa_zones.run, minutes=30, run_now=False, next_run_time=datetime.now(timezone.utc) + timedelta(minutes=4))
    add_job("adsb", adsb.run, minutes=1)
    add_job("watchdog", watchdog.run, minutes=30, run_now=False)
    add_job("backup", backup.tick, minutes=5, run_now=False)
    ais.start()
    # Index and tripwires, shortly after the collectors have had a chance to run
    add_job("scoring", scoring.run, minutes=10, run_now=False, next_run_time=datetime.now(timezone.utc) + timedelta(minutes=2))
    add_job("llm_analyze", analyze.run, minutes=30, run_now=False, next_run_time=datetime.now(timezone.utc) + timedelta(minutes=3))
    add_job("digest", digest.run, CronTrigger(hour=LLM["digest_hour"], minute=LLM["digest_minute"], timezone=LOCAL_TZ), run_now=False)
    scheduler.start()
