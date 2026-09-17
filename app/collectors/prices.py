"""Prices via yfinance: 5-minute bars (30 days kept) and daily closes (2 years)."""
from contextlib import closing
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import yfinance as yf

from app import db
from app.config import SETTINGS

SYMBOLS = SETTINGS["prices"]["symbols"]
MARKETS = [  # (timezone, open, close) regular sessions, Monday to Friday
    (ZoneInfo("Asia/Taipei"), time(9, 0), time(13, 30)),
    (ZoneInfo("America/New_York"), time(9, 30), time(16, 0)),
]


def market_open(now: datetime) -> bool:
    for tz, start, end in MARKETS:
        local = now.astimezone(tz)
        if local.weekday() < 5 and start <= local.time() <= end:
            return True
    return False


def closes(frame):
    """Yield (symbol, timestamp, close) from a multi-ticker yfinance frame."""
    for symbol in SYMBOLS:
        if symbol not in frame.columns.get_level_values(0):
            continue
        for ts, close in frame[symbol]["Close"].dropna().items():
            yield symbol, ts, float(close)


def run() -> str:
    now = datetime.now(timezone.utc)
    # Every 5 minutes while Taiwan or US markets are open, otherwise every 15
    if not market_open(now) and now.minute % 15 >= 5:
        return "skipped (markets closed)"
    with closing(db.connect()) as conn:
        have_daily = conn.execute("SELECT COUNT(*) FROM prices_daily").fetchone()[0] > 0

    intraday = yf.download(SYMBOLS, period="5d", interval="5m", group_by="ticker", progress=False, auto_adjust=True)
    daily = yf.download(SYMBOLS, period="10d" if have_daily else "2y", interval="1d", group_by="ticker",
                        progress=False, auto_adjust=True)
    cutoff = (now - timedelta(days=30)).isoformat(timespec="seconds")
    with closing(db.connect()) as conn, conn:
        bars = [(s, ts.tz_convert("UTC").isoformat(), c) for s, ts, c in closes(intraday)]
        conn.executemany("INSERT OR REPLACE INTO prices_intraday (symbol, ts_utc, close) VALUES (?, ?, ?)", bars)
        days = [(s, ts.date().isoformat(), c) for s, ts, c in closes(daily)]
        # Yahoo has no daily history for some FX pairs (CNH=X): derive the day's close from the last 5m bar
        sparse = [s for s in SYMBOLS if sum(1 for d in days if d[0] == s) < 2]
        derived = {}
        for s, ts, c in closes(intraday):
            if s in sparse:
                derived[(s, ts.tz_convert("UTC").date().isoformat())] = c
        days += [(s, day, c) for (s, day), c in derived.items()]
        conn.executemany("INSERT OR REPLACE INTO prices_daily (symbol, day, close) VALUES (?, ?, ?)", days)
        conn.execute("DELETE FROM prices_intraday WHERE ts_utc < ?", (cutoff,))
    symbols_seen = {b[0] for b in bars}
    missing = [s for s in SYMBOLS if s not in symbols_seen]
    if missing:
        raise RuntimeError(f"no intraday data for {', '.join(missing)}")
    return f"{len(bars)} bars, {len(days)} daily closes"
