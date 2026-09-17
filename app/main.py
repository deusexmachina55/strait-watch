import logging
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import PlainTextResponse
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from app import backup, config, db, scheduler
from app.alerts import telegram
from app.web import data

# httpx logs request URLs at INFO, which would include the bot token
logging.getLogger("httpx").setLevel(logging.WARNING)

STARTED = time.monotonic()
HEARTBEAT_STALE_SECONDS = 180
basic = HTTPBasic()
templates = Jinja2Templates(directory=config.ROOT / "app" / "web" / "templates")
templates.env.globals["fmt_price"] = data.fmt_price
STATIC = config.ROOT / "app" / "web" / "static"
GROUPS = config.SETTINGS["prices"]["groups"]
OVERVIEW_SYMBOLS = config.SETTINGS["prices"]["overview"]
MSA_REGIONS = [c["region"] for c in config.SETTINGS["msa"]["channels"]]


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
    jobs = data.jobs()
    beat = jobs.get("heartbeat")
    healthy = beat is not None and beat["ok"] and beat["age_seconds"] < HEARTBEAT_STALE_SECONDS
    return {"status": "ok" if healthy else "stale", "time": local_time(datetime.now(timezone.utc)),
            "uptime_seconds": int(time.monotonic() - STARTED), "jobs": jobs}


def render(request: Request, template: str, page: str, **ctx):
    return templates.TemplateResponse(request, template, status() | {"page": page} | ctx)


# Page contexts, shared by the full page and its htmx partial

def overview_ctx() -> dict:
    return {"scores": data.scores(), "changes": data.price_changes(["GC=F", "TSM", "BTC-USD"]), "pla": data.pla(),
            "gdelt": data.gdelt(), "msa": data.msa(), "prices": data.prices(OVERVIEW_SYMBOLS),
            "advisories": data.advisories(), "tripwires": data.tripwires(10), "items": data.items(limit=15), "mapsum": data.map_summary()}


def news_ctx(source: str, all: bool, q: str, limit: int) -> dict:
    return {"items": data.items(source, all, q.strip(), min(limit, 1000))}


def markets_ctx() -> dict:
    return {"groups": [{"name": g["name"], "instruments": data.prices(g["symbols"]), "changes": data.price_changes(g["symbols"])}
                       for g in GROUPS]}


def warnings_ctx(region: str, military: bool) -> dict:
    return {"warnings": data.msa_warnings(region, military), "notices": data.notices(), "tripwires": data.tripwires(50)}


def signals_ctx() -> dict:
    return {"odds": data.odds(), "odds_history": data.odds_history(), "advisories": data.advisories(),
            "advisory_history": data.advisory_history()}


def system_ctx() -> dict:
    return {"failures": data.failures(), "db_size": data.db_size_mb(), "llm": data.llm_usage(),
            "last_backup": next((b for b in data.backups() if b["ok"]), None)}


@app.get("/")
def overview(request: Request):
    return render(request, "overview.html", "Overview", **overview_ctx())


@app.get("/partials/overview")
def overview_partial(request: Request):
    return render(request, "partials/overview.html", "Overview", **overview_ctx())


@app.get("/news")
def news(request: Request):
    return render(request, "news.html", "News", sources=data.sources(), **news_ctx("", False, "", 100))


@app.get("/partials/news")
def news_partial(request: Request, source: str = "", all: bool = False, q: str = "", limit: int = 100):
    return render(request, "partials/news.html", "News", **news_ctx(source, all, q, limit))


@app.get("/markets")
def markets(request: Request):
    return render(request, "markets.html", "Markets", **markets_ctx())


@app.get("/partials/markets")
def markets_partial(request: Request):
    return render(request, "partials/markets.html", "Markets", **markets_ctx())


@app.get("/warnings")
def warnings(request: Request):
    return render(request, "warnings.html", "Warnings", regions=MSA_REGIONS, **warnings_ctx("", True))


@app.get("/partials/warnings")
def warnings_partial(request: Request, region: str = "", military: bool = False):
    return render(request, "partials/warnings.html", "Warnings", **warnings_ctx(region, military))


@app.get("/signals")
def signals(request: Request):
    return render(request, "signals.html", "Signals", **signals_ctx())


@app.get("/partials/signals")
def signals_partial(request: Request):
    return render(request, "partials/signals.html", "Signals", **signals_ctx())


@app.get("/map")
def map_page(request: Request):
    return render(request, "map.html", "Map", mapsum=data.map_summary())


@app.get("/api/map")
def map_api(day: str = "", days: int = 90):
    return data.map_data(day or None, min(days, 365))


def backup_ctx() -> dict:
    history = data.backups()
    return {"history": history, "last_ok": next((b for b in history if b["ok"]), None), "db_size": data.db_size_mb()}


@app.get("/backup")
def backup_page(request: Request):
    return render(request, "backup.html", "Backup", cfg=backup.settings(), **backup_ctx())


@app.get("/partials/backup")
def backup_partial(request: Request):
    return render(request, "partials/backup.html", "Backup", **backup_ctx())


def htmx_only(request: Request) -> None:
    # Only htmx-issued posts carry this header; blocks cross-site form posts reusing cached basic auth
    if request.headers.get("hx-request") != "true":
        raise HTTPException(403)


@app.post("/backup/settings", response_class=PlainTextResponse)
def backup_settings(request: Request, backup_dir: str = Form(""), backup_time: str = Form("02:30"), backup_enabled: str = Form("0"),
                    keep_daily: int = Form(7), keep_weekly: int = Form(4), keep_monthly: int = Form(12)):
    htmx_only(request)
    backup.save_settings({"backup_dir": backup_dir, "backup_time": backup_time, "backup_enabled": "1" if backup_enabled == "1" else "0",
                          "keep_daily": keep_daily, "keep_weekly": keep_weekly, "keep_monthly": keep_monthly})
    return "Saved."


@app.post("/backup/test", response_class=PlainTextResponse)
def backup_test(request: Request, backup_dir: str = Form("")):
    htmx_only(request)
    try:
        return backup.test_destination(backup_dir)
    except Exception as e:
        return f"Failed: {e}"


@app.post("/backup/run", response_class=PlainTextResponse)
def backup_run(request: Request):
    htmx_only(request)
    try:
        return "Done: " + backup.run(manual=True)
    except Exception as e:
        return f"Failed: {e}"


@app.get("/system")
def system(request: Request):
    return render(request, "system.html", "System", **system_ctx())


@app.get("/partials/system")
def system_partial(request: Request):
    return render(request, "partials/system.html", "System", **system_ctx())


@app.get("/health")
def health():
    return status()


# A StaticFiles mount would bypass the app-level auth dependency
@app.get("/static/{name}")
def static(name: str):
    path = STATIC / name
    if "/" in name or "\\" in name or not path.is_file():
        raise HTTPException(404)
    return FileResponse(path)


@app.post("/api/test-alert")
def test_alert(request: Request):
    # Custom header blocks cross-site form posts that would reuse cached basic auth
    if request.headers.get("x-requested-with") != "strait-watch":
        raise HTTPException(403)
    telegram.send(f"Strait Watch test alert, {local_time(datetime.now(timezone.utc))}")
    return {"sent": True}
