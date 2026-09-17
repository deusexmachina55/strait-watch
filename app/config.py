import os
import tomllib
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DB_PATH = Path(os.environ.get("STRAIT_DB", ROOT / "data" / "strait.db"))
LOCAL_TZ = ZoneInfo("Asia/Singapore")
SETTINGS = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))


def required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"{name} is not set in .env")
    return value


TELEGRAM_BOT_TOKEN = required("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = required("TELEGRAM_CHAT_ID")
BASIC_AUTH_USER = required("BASIC_AUTH_USER")
BASIC_AUTH_PASS = required("BASIC_AUTH_PASS")
