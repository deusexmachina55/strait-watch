import logging
import secrets
import time
from contextlib import asynccontextmanager, closing
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from app import config, db, scheduler
from app.alerts import telegram

# httpx logs request URLs at INFO, which would include the bot token
logging.getLogger("httpx").setLevel(logging.WARNING)

STARTED = time.monotonic()
HEARTBEAT_STALE_SECONDS = 180
basic = HTTPBasic()
templates = Jinja2Templates(directory=config.ROOT / "app" / "web" / "templates")


def auth(credentials: HTTPBasicCredentials = Depends(basic)) -> None:
    user_ok = secrets.compare_digest(credentials.username.encode(), config.BASIC_AUTH_USER.encode())
    pass_ok = secrets.compare_digest(credentials.password.encode(), config.BASIC_AUTH_PASS.encode())
    if not (user_ok and pass_ok):
        raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    scheduler.start()
    yield
    scheduler.scheduler.shutdown(wait=False)


app = FastAPI(lifespan=lifespan, dependencies=[Depends(auth)], docs_url=None, redoc_url=None, openapi_url=None)


def local_time(dt: datetime) -> str:
    return dt.astimezone(config.LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S SGT")


def status() -> dict:
    now = datetime.now(timezone.utc)
    with closing(db.connect()) as conn:
        rows = conn.execute("SELECT job, last_run_utc, ok, message FROM job_status ORDER BY job").fetchall()
    jobs = {}
    for r in rows:
        last = datetime.fromisoformat(r["last_run_utc"])
        jobs[r["job"]] = {
            "last_run": local_time(last),
            "age_seconds": int((now - last).total_seconds()),
            "ok": bool(r["ok"]),
            "message": r["message"],
        }
    beat = jobs.get("heartbeat")
    healthy = beat is not None and beat["ok"] and beat["age_seconds"] < HEARTBEAT_STALE_SECONDS
    return {
        "status": "ok" if healthy else "stale",
        "time": local_time(now),
        "uptime_seconds": int(time.monotonic() - STARTED),
        "jobs": jobs,
    }


@app.get("/")
def dashboard(request: Request):
    return templates.TemplateResponse(request, "dashboard.html", status())


@app.get("/health")
def health():
    return status()


@app.post("/api/test-alert")
def test_alert(request: Request):
    # Custom header blocks cross-site form posts that would reuse cached basic auth
    if request.headers.get("x-requested-with") != "strait-watch":
        raise HTTPException(403)
    telegram.send(f"Strait Watch test alert, {local_time(datetime.now(timezone.utc))}")
    return {"sent": True}
