import logging
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import FileResponse
from fastapi.templating import Jinja2Templates

from app import config, db, scheduler
from app.alerts import telegram
from app.web import data

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
STATIC = config.ROOT / "app" / "web" / "static"


# A StaticFiles mount would bypass the app-level auth dependency
@app.get("/static/{name}")
def static(name: str):
    path = STATIC / name
    if "/" in name or "\\" in name or not path.is_file():
        raise HTTPException(404)
    return FileResponse(path)


def local_time(dt: datetime) -> str:
    return dt.astimezone(config.LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S SGT")


def status() -> dict:
    jobs = data.jobs()
    beat = jobs.get("heartbeat")
    healthy = beat is not None and beat["ok"] and beat["age_seconds"] < HEARTBEAT_STALE_SECONDS
    return {
        "status": "ok" if healthy else "stale",
        "time": local_time(datetime.now(timezone.utc)),
        "uptime_seconds": int(time.monotonic() - STARTED),
        "jobs": jobs,
    }


@app.get("/")
def dashboard(request: Request):
    ctx = status() | {
        "pla": data.pla(), "gdelt": data.gdelt(), "msa": data.msa(), "prices": data.prices(),
        "odds": data.odds(), "advisories": data.advisories(), "sources": data.sources(),
        "items": data.items(), "source": "", "show_all": False,
    }
    return templates.TemplateResponse(request, "dashboard.html", ctx)


@app.get("/items")
def items(request: Request, source: str = "", all: bool = False):
    return templates.TemplateResponse(request, "items.html", {"items": data.items(source, all)})


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
