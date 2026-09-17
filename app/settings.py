"""Per-install settings and secrets, stored in the settings table so they can be managed from the browser.

Values in .env are imported once on first start (legacy installs), after which the database wins."""
import base64
import hashlib
import hmac
import os
import secrets as pysecrets
from contextlib import closing

from app import db
from app.config import DB_PATH

# key -> (env var to import from, secret)
KEYS = {
    "admin_user": ("BASIC_AUTH_USER", False),
    "admin_pass_hash": (None, True),
    "telegram_bot_token": ("TELEGRAM_BOT_TOKEN", True),
    "telegram_chat_id": ("TELEGRAM_CHAT_ID", False),
    "gemini_api_key": ("GEMINI_API_KEY", True),
    "groq_api_key": ("GROQ_API_KEY", True),
    "openrouter_api_key": ("OPENROUTER_API_KEY", True),
    "aisstream_api_key": ("AISSTREAM_API_KEY", True),
    "backup_smb_user": ("BACKUP_SMB_USER", False),
    "backup_smb_pass": ("BACKUP_SMB_PASS", True),
    "timezone": ("STRAIT_TZ", False),
}
INITIAL_PASSWORD_FILE = DB_PATH.parent / "initial-password.txt"


def get(key: str) -> str:
    with closing(db.connect()) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else ""


def get_all() -> dict:
    with closing(db.connect()) as conn:
        stored = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}
    return {k: stored.get(k, "") for k in KEYS}


def set_values(values: dict) -> None:
    with closing(db.connect()) as conn, conn:
        for k, v in values.items():
            if k in KEYS:
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, (v or "").strip()))


def masked(value: str) -> str:
    if not value:
        return ""
    return "•" * 8 + value[-4:] if len(value) > 8 else "•" * len(value)


# Password hashing: pbkdf2-sha256, stored as iterations$salt$hash

def hash_password(password: str) -> str:
    salt = pysecrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return f"200000${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        iterations, salt, digest = stored.split("$")
        expected = base64.b64decode(digest)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), int(iterations))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def import_env_once() -> list[str]:
    """Copy values from the environment (.env) into empty settings. Returns the keys imported."""
    current = get_all()
    imported = {}
    for key, (env, _) in KEYS.items():
        if env and not current[key] and os.environ.get(env):
            imported[key] = os.environ[env]
    if not current["admin_pass_hash"] and os.environ.get("BASIC_AUTH_PASS"):
        imported["admin_pass_hash"] = hash_password(os.environ["BASIC_AUTH_PASS"])
    if imported:
        set_values(imported)
    return sorted(imported)


def ensure_admin() -> str | None:
    """Make sure a login exists. On a fresh install, generate a password and leave it in data\\initial-password.txt."""
    current = get_all()
    if not current["admin_user"]:
        set_values({"admin_user": "admin"})
    if current["admin_pass_hash"]:
        return None
    password = pysecrets.token_urlsafe(12)
    set_values({"admin_pass_hash": hash_password(password)})
    INITIAL_PASSWORD_FILE.write_text(f"Strait Watch initial login\nuser: {current['admin_user'] or 'admin'}\npassword: {password}\n"
                                     f"Change it on the Settings page, then delete this file.\n")
    return password


def status() -> dict:
    s = get_all()
    llm = [name for name in ("gemini", "groq", "openrouter") if s[f"{name}_api_key"]]
    return {
        "alerts": bool(s["telegram_bot_token"] and s["telegram_chat_id"]),
        "telegram_token": bool(s["telegram_bot_token"]),
        "llm": llm,
        "ships": bool(s["aisstream_api_key"]),
        "smb": bool(s["backup_smb_user"]),
        "timezone": s["timezone"] or "Asia/Singapore",
        "initial_password_file": INITIAL_PASSWORD_FILE.exists(),
    }
