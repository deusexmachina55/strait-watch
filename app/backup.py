"""Nightly SQLite snapshot (VACUUM INTO) copied to a user-chosen drive or UNC path, with 7/4/12 retention.

Settings live in the database (the service cannot write config.toml). SMB credentials, if the share
needs them, come from .env as BACKUP_SMB_USER and BACKUP_SMB_PASS."""
import os
import shutil
import subprocess
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from app import db, settings as app_settings
from app.config import DB_PATH, LOCAL_TZ

DEFAULTS = {"backup_dir": "", "backup_time": "02:30", "keep_daily": "7", "keep_weekly": "4", "keep_monthly": "12", "backup_enabled": "0"}
LOCAL_DIR = DB_PATH.parent / "backups"
LOCAL_KEEP = 3
_lock = threading.Lock()


def settings() -> dict:
    with closing(db.connect()) as conn:
        stored = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}
    return DEFAULTS | stored


def save_settings(values: dict) -> None:
    with closing(db.connect()) as conn, conn:
        for k, v in values.items():
            if k in DEFAULTS:
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, str(v).strip()))


def connect_share(dest: Path) -> str:
    """Map a UNC share for the service account when credentials are configured. Returns a note for the log."""
    user, pw = app_settings.get("backup_smb_user"), app_settings.get("backup_smb_pass")
    if not str(dest).startswith("\\\\") or not user:
        return ""
    parts = str(dest).strip("\\").split("\\")
    share = "\\\\" + "\\".join(parts[:2])
    r = subprocess.run(["net", "use", share, pw or "", f"/user:{user}", "/persistent:no"], capture_output=True, text=True, timeout=60)
    # 1219: already connected with different credentials, which still works
    return "" if r.returncode == 0 or "1219" in r.stderr + r.stdout else f"net use: {(r.stderr or r.stdout).strip()[:120]}"


def test_destination(path: str) -> str:
    dest = Path(path.strip())
    if not path.strip():
        raise ValueError("No destination set")
    note = connect_share(dest)
    dest.mkdir(parents=True, exist_ok=True)
    probe = dest / ".strait-watch-write-test"
    probe.write_text(datetime.now(timezone.utc).isoformat())
    probe.unlink()
    free = shutil.disk_usage(dest).free / 1_073_741_824
    return f"OK, writable, {free:.1f} GB free" + (f" ({note})" if note else "")


def prune(folder: Path, keep: int) -> None:
    files = sorted(f for f in folder.glob("strait-*.db") if "pre-restore" not in f.name)
    for f in files[:-keep] if keep > 0 else files:
        f.unlink()


def record(path: str, size_mb: float, ok: bool, message: str) -> None:
    with closing(db.connect()) as conn, conn:
        conn.execute("INSERT INTO backups (ts_utc, path, size_mb, ok, message) VALUES (?, ?, ?, ?, ?)",
                     (datetime.now(timezone.utc).isoformat(timespec="seconds"), path, size_mb, ok, message))


def run(manual: bool = False) -> str:
    if not _lock.acquire(blocking=False):
        return "backup already running"
    try:
        return _run(manual)
    finally:
        _lock.release()


def _run(manual: bool) -> str:
    cfg = settings()
    now = datetime.now(LOCAL_TZ)
    LOCAL_DIR.mkdir(exist_ok=True)
    local = LOCAL_DIR / f"strait-{now:%Y%m%d-%H%M%S}.db"
    local.unlink(missing_ok=True)
    with closing(db.connect()) as conn:
        conn.execute("VACUUM INTO ?", (str(local),))
    prune(LOCAL_DIR, LOCAL_KEEP)
    size = round(local.stat().st_size / 1_048_576, 1)
    if not cfg["backup_dir"]:
        record(str(local), size, True, "local snapshot only, no destination set")
        return f"local snapshot {local.name} ({size} MB), no destination set"
    dest = Path(cfg["backup_dir"])
    try:
        note = connect_share(dest)
        targets = [dest / "daily"]
        if now.weekday() == 6 or manual and not (dest / "weekly").exists():
            targets.append(dest / "weekly")
        if now.day == 1 or manual and not (dest / "monthly").exists():
            targets.append(dest / "monthly")
        for t in targets:
            t.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local, t / local.name)
        prune(dest / "daily", int(cfg["keep_daily"]))
        prune(dest / "weekly", int(cfg["keep_weekly"]))
        prune(dest / "monthly", int(cfg["keep_monthly"]))
    except Exception as e:
        record(str(dest), size, False, repr(e)[:300])
        raise
    msg = f"{local.name} ({size} MB) to {dest} [{', '.join(t.name for t in targets)}]" + (f" ({note})" if note else "")
    record(str(dest / "daily" / local.name), size, True, msg)
    return msg


def due() -> bool:
    """True once a day at or after the configured time, when enabled and not yet done today."""
    cfg = settings()
    if cfg["backup_enabled"] != "1":
        return False
    now = datetime.now(LOCAL_TZ)
    if now.strftime("%H:%M") < cfg["backup_time"]:
        return False
    start = now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat(timespec="seconds")
    with closing(db.connect()) as conn:
        return conn.execute("SELECT 1 FROM backups WHERE ok = 1 AND ts_utc >= ? AND path LIKE ?", (start, f"%{os.sep}daily{os.sep}%")).fetchone() is None


def tick() -> str:
    return run() if due() else "not due"


# Restore

REQUIRED_TABLES = {"items", "pla_daily", "scores", "job_status"}


def list_snapshots() -> list[dict]:
    """Snapshots in data\backups and at the destination, newest first."""
    folders = [LOCAL_DIR]
    dest = settings()["backup_dir"]
    if dest:
        folders += [Path(dest) / sub for sub in ("daily", "weekly", "monthly")]
    out = []
    for folder in folders:
        try:
            files = list(folder.glob("strait-*.db"))
        except OSError:
            continue
        for f in files:
            st = f.stat()
            out.append({"path": str(f), "size_mb": round(st.st_size / 1_048_576, 1),
                        "when": datetime.fromtimestamp(st.st_mtime, LOCAL_TZ).strftime("%Y-%m-%d %H:%M"),
                        "where": "local" if folder == LOCAL_DIR else folder.name})
    return sorted(out, key=lambda s: s["when"], reverse=True)


def restore(path: str) -> str:
    """Copy a snapshot's pages into the live database. Reversible: a safety snapshot is taken first."""
    import sqlite3
    from app.scheduler import scheduler
    src_path = Path(path.strip())
    if not src_path.is_file():
        raise ValueError(f"not a file: {src_path}")
    connect_share(src_path)
    src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    try:
        if src.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("snapshot failed integrity check")
        tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if not REQUIRED_TABLES <= tables:
            raise ValueError(f"snapshot is missing tables: {', '.join(sorted(REQUIRED_TABLES - tables))}")
        with _lock:
            scheduler.pause()
            try:
                LOCAL_DIR.mkdir(exist_ok=True)
                safety = LOCAL_DIR / f"strait-{datetime.now(LOCAL_TZ):%Y%m%d-%H%M%S}-pre-restore.db"
                with closing(db.connect()) as live:
                    live.execute("VACUUM INTO ?", (str(safety),))
                    src.backup(live)
            finally:
                scheduler.resume()
    finally:
        src.close()
    size = round(src_path.stat().st_size / 1_048_576, 1)
    msg = f"restored from {src_path} ({size} MB); previous database kept as {safety.name}"
    record(str(src_path), size, True, msg)
    return msg
