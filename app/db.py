import sqlite3
from contextlib import closing

from app.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS job_status (
    job TEXT PRIMARY KEY,
    last_run_utc TEXT NOT NULL,
    ok INTEGER NOT NULL,
    message TEXT
);
CREATE TABLE IF NOT EXISTS job_failures (
    id INTEGER PRIMARY KEY,
    job TEXT NOT NULL,
    ran_at_utc TEXT NOT NULL,
    message TEXT
);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init() -> None:
    DB_PATH.parent.mkdir(exist_ok=True)
    with closing(connect()) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
