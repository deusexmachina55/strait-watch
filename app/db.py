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

-- News and notices from RSS, Coast Guard and Japan MOD
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    url TEXT NOT NULL,
    canonical_url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    title_hash TEXT NOT NULL UNIQUE,
    published_utc TEXT NOT NULL,
    fetched_utc TEXT NOT NULL,
    snippet TEXT,
    tags TEXT,
    relevant INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS items_published ON items (published_utc);

-- Taiwan MND daily report, covering the 24h to 06:00 Taiwan time on report_date
CREATE TABLE IF NOT EXISTS pla_daily (
    report_date TEXT PRIMARY KEY,
    aircraft INTEGER NOT NULL,
    aircraft_entered INTEGER NOT NULL,
    median_line_crossed INTEGER NOT NULL,
    navy_ships INTEGER NOT NULL,
    official_ships INTEGER NOT NULL,
    balloons INTEGER NOT NULL,
    url TEXT NOT NULL,
    fetched_utc TEXT NOT NULL
);

-- GDELT 2.0 event counts per UTC day and country dyad
CREATE TABLE IF NOT EXISTS gdelt_daily (
    day TEXT NOT NULL,
    dyad TEXT NOT NULL,
    events INTEGER NOT NULL DEFAULT 0,
    verbal_conflict INTEGER NOT NULL DEFAULT 0,
    material_conflict INTEGER NOT NULL DEFAULT 0,
    force_posture INTEGER NOT NULL DEFAULT 0,
    coerce INTEGER NOT NULL DEFAULT 0,
    fight INTEGER NOT NULL DEFAULT 0,
    goldstein_sum REAL NOT NULL DEFAULT 0,
    tone_sum REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (day, dyad)
);
CREATE TABLE IF NOT EXISTS gdelt_files (
    name TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    processed_utc TEXT NOT NULL
);

-- China MSA navigation warnings
CREATE TABLE IF NOT EXISTS msa_warnings (
    url TEXT PRIMARY KEY,
    region TEXT NOT NULL,
    number TEXT,
    title TEXT NOT NULL,
    issued_date TEXT NOT NULL,
    military INTEGER NOT NULL,
    fetched_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS msa_issued ON msa_warnings (issued_date);

-- US State Department travel advisories
CREATE TABLE IF NOT EXISTS advisories (
    title TEXT NOT NULL,
    country TEXT NOT NULL,
    level INTEGER NOT NULL,
    published_utc TEXT NOT NULL,
    url TEXT NOT NULL,
    PRIMARY KEY (title, published_utc)
);

-- Polymarket probability snapshots
CREATE TABLE IF NOT EXISTS market_odds (
    market_id TEXT NOT NULL,
    question TEXT NOT NULL,
    ts_utc TEXT NOT NULL,
    probability REAL NOT NULL,
    volume REAL,
    PRIMARY KEY (market_id, ts_utc)
);

-- Phase 3: daily index and tripwire log
CREATE TABLE IF NOT EXISTS scores (
    day TEXT PRIMARY KEY,
    composite REAL NOT NULL,
    military REAL NOT NULL,
    economic REAL NOT NULL,
    diplomatic REAL NOT NULL,
    rhetoric REAL NOT NULL,
    details TEXT NOT NULL,
    computed_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tripwire_log (
    id INTEGER PRIMARY KEY,
    tripwire TEXT NOT NULL,
    fired_utc TEXT NOT NULL,
    severity INTEGER NOT NULL,
    detail TEXT NOT NULL,
    url TEXT,
    alerted INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS tripwire_fired ON tripwire_log (tripwire, fired_utc);

-- Prices: 5-minute bars (rolling) and daily closes
CREATE TABLE IF NOT EXISTS prices_intraday (
    symbol TEXT NOT NULL,
    ts_utc TEXT NOT NULL,
    close REAL NOT NULL,
    PRIMARY KEY (symbol, ts_utc)
);
CREATE TABLE IF NOT EXISTS prices_daily (
    symbol TEXT NOT NULL,
    day TEXT NOT NULL,
    close REAL NOT NULL,
    PRIMARY KEY (symbol, day)
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
