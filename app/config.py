import os
import sqlite3
import tomllib
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DB_PATH = Path(os.environ.get("STRAIT_DB", ROOT / "data" / "strait.db"))
SETTINGS = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))


def _timezone() -> ZoneInfo:
    """Display timezone from the settings table (set on the Settings page, applied on restart), else .env, else Singapore."""
    name = os.environ.get("STRAIT_TZ", "")
    if DB_PATH.exists():
        try:
            with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as conn:
                row = conn.execute("SELECT value FROM settings WHERE key = 'timezone'").fetchone()
                name = row[0] if row and row[0] else name
        except sqlite3.Error:
            pass
    try:
        return ZoneInfo(name or "Asia/Singapore")
    except Exception:
        return ZoneInfo("Asia/Singapore")


LOCAL_TZ = _timezone()
TZ_LABEL = os.environ.get("STRAIT_TZ_LABEL") or ("SGT" if LOCAL_TZ.key == "Asia/Singapore" else LOCAL_TZ.key)
