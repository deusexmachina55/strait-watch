import logging
from contextlib import closing
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app import db

log = logging.getLogger("uvicorn.error")
scheduler = AsyncIOScheduler(timezone=timezone.utc)


def record(job: str, ok: bool, message: str | None = None) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(db.connect()) as conn, conn:
        conn.execute(
            "INSERT INTO job_status (job, last_run_utc, ok, message) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(job) DO UPDATE SET last_run_utc = excluded.last_run_utc, "
            "ok = excluded.ok, message = excluded.message",
            (job, now, ok, message),
        )
        if not ok:
            conn.execute(
                "INSERT INTO job_failures (job, ran_at_utc, message) VALUES (?, ?, ?)",
                (job, now, message),
            )


def add_job(name: str, fn, **interval) -> None:
    def run():
        try:
            fn()
        except Exception as e:
            log.error("job %s failed: %r", name, e)
            record(name, False, repr(e))
            return
        record(name, True)

    scheduler.add_job(
        run, "interval", id=name, replace_existing=True, max_instances=1, coalesce=True,
        next_run_time=datetime.now(timezone.utc), **interval,
    )


def heartbeat() -> None:
    pass


def start() -> None:
    add_job("heartbeat", heartbeat, seconds=60)
    scheduler.start()
