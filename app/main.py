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

from app import backup, config, db, scheduler, settings
from app.llm import briefs as briefs_mod
from app.alerts import telegram
from app.web import data

# httpx logs request URLs at INFO, which would include the bot token
logging.getLogger("httpx").setLevel(logging.WARNING)

STARTED = time.monotonic()
HEARTBEAT_STALE_SECONDS = 180
basic = HTTPBasic()
templates = Jinja2Templates(directory=config.ROOT / "app" / "web" / "templates")
templates.env.globals["fmt_price"] = data.fmt_price
templates.env.globals["tz_label"] = config.TZ_LABEL
STATIC = config.ROOT / "app" / "web" / "static"
GROUPS = config.SETTINGS["prices"]["groups"]
OVERVIEW_SYMBOLS = config.SETTINGS["prices"]["overview"]
MSA_REGIONS = [c["region"] for c in config.SETTINGS["msa"]["channels"]]


def auth(credentials: HTTPBasicCredentials = Depends(basic)) -> None:
    user_ok = secrets.compare_digest(credentials.username.encode(), settings.get("admin_user").encode())
    pass_ok = settings.verify_password(credentials.password, settings.get("admin_pass_hash"))
    if not (user_ok and pass_ok):
        raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    imported = settings.import_env_once()
    if imported:
        logging.getLogger("uvicorn.error").info("settings imported from .env: %s", ", ".join(imported))
    if settings.ensure_admin():
        logging.getLogger("uvicorn.error").warning("no admin password was set; a generated one is in %s", settings.INITIAL_PASSWORD_FILE)
    scheduler.start()
    yield
    scheduler.scheduler.shutdown(wait=False)


app = FastAPI(lifespan=lifespan, dependencies=[Depends(auth)], docs_url=None, redoc_url=None, openapi_url=None)


def local_time(dt: datetime) -> str:
    return dt.astimezone(config.LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S ") + config.TZ_LABEL


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
            "last_backup": next((b for b in data.backups() if b["ok"]), None), "st": settings.status()}


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
    return render(request, "backup.html", "Backup", cfg=backup.settings(), snapshots=backup.list_snapshots(), **backup_ctx())


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


@app.post("/backup/restore", response_class=PlainTextResponse)
def backup_restore(request: Request, snapshot: str = Form(""), path: str = Form("")):
    htmx_only(request)
    try:
        return "Done: " + backup.restore(path.strip() or snapshot)
    except Exception as e:
        return f"Failed: {e}"


def briefing_ctx(kind: str = "") -> dict:
    return {"mech": data.mechanical(), "premium": data.risk_premium(), "briefs": data.briefs(kind), "scorecard": data.scorecard(), "kind": kind}


@app.get("/briefing")
def briefing(request: Request, kind: str = ""):
    return render(request, "briefing.html", "Briefing", **briefing_ctx(kind))


@app.get("/partials/briefing")
def briefing_partial(request: Request, kind: str = ""):
    return render(request, "partials/briefing.html", "Briefing", **briefing_ctx(kind))


@app.post("/briefing/generate", response_class=PlainTextResponse)
def briefing_generate(request: Request, kind: str = Form("weekly")):
    htmx_only(request)
    if kind not in ("weekly", "monthly"):
        raise HTTPException(400)
    try:
        return "Done: " + briefs_mod.generate(kind) + ". Reload to read it."
    except Exception as e:
        return f"Failed: {e}"


# Settings

@app.get("/settings")
def settings_page(request: Request):
    s = settings.get_all()
    return render(request, "settings.html", "Settings", s=s, m={k: settings.masked(v) for k, v in s.items()}, st=settings.status())


def keep_or_new(form_value: str, key: str) -> str:
    return form_value.strip() if form_value and form_value.strip() else settings.get(key)


@app.post("/settings/password", response_class=PlainTextResponse)
def settings_password(request: Request, admin_user: str = Form("admin"), current: str = Form(""), new: str = Form(""), confirm: str = Form("")):
    htmx_only(request)
    if not settings.verify_password(current, settings.get("admin_pass_hash")):
        return "Current password is wrong."
    if len(new) < 16:
        return "New password must be at least 16 characters."
    if new != confirm:
        return "New password and confirmation differ."
    if not admin_user.strip():
        return "Username cannot be empty."
    settings.set_values({"admin_user": admin_user, "admin_pass_hash": settings.hash_password(new)})
    settings.INITIAL_PASSWORD_FILE.unlink(missing_ok=True)
    return "Changed. Reload and log in with the new password."


@app.post("/settings/telegram", response_class=PlainTextResponse)
def settings_telegram(request: Request, telegram_bot_token: str = Form("")):
    htmx_only(request)
    token = keep_or_new(telegram_bot_token, "telegram_bot_token")
    if not token:
        return "No token given."
    try:
        me = telegram.get_me(token)
    except Exception as e:
        return f"Token rejected: {e}"
    settings.set_values({"telegram_bot_token": token})
    chat = settings.get("telegram_chat_id")
    return f"Saved. Bot @{me.get('username')} works. " + (f"Chat {chat} set." if chat else "Now message the bot, then press Detect chat.")


@app.post("/settings/telegram/detect", response_class=PlainTextResponse)
def settings_telegram_detect(request: Request):
    htmx_only(request)
    token = settings.get("telegram_bot_token")
    if not token:
        return "Save a bot token first."
    try:
        chat = telegram.detect_chat(token)
    except Exception as e:
        return f"Failed: {e}"
    if not chat:
        return "No message found. Send any message to the bot in Telegram, then press Detect chat again."
    settings.set_values({"telegram_chat_id": str(chat["id"])})
    name = chat.get("username") or chat.get("title") or chat.get("first_name") or ""
    return f"Chat {chat['id']} ({name}) saved. Alerts are on."


@app.post("/settings/telegram/send", response_class=PlainTextResponse)
def settings_telegram_send(request: Request):
    htmx_only(request)
    try:
        telegram.send(f"Strait Watch test message, {local_time(datetime.now(timezone.utc))}")
        return "Sent."
    except Exception as e:
        return f"Failed: {e}"


@app.post("/settings/llm", response_class=PlainTextResponse)
def settings_llm(request: Request, groq_api_key: str = Form(""), gemini_api_key: str = Form(""), openrouter_api_key: str = Form("")):
    htmx_only(request)
    from app.llm import chain
    values = {"groq_api_key": keep_or_new(groq_api_key, "groq_api_key"), "gemini_api_key": keep_or_new(gemini_api_key, "gemini_api_key"),
              "openrouter_api_key": keep_or_new(openrouter_api_key, "openrouter_api_key")}
    settings.set_values(values)
    results = []
    for name in ("gemini", "groq", "openrouter"):
        if not values[f"{name}_api_key"]:
            results.append(f"{name}: not set")
            continue
        try:
            chain.probe(name)
            results.append(f"{name}: OK")
        except Exception as e:
            results.append(f"{name}: failed ({str(e)[:80]})")
    return "Saved. " + "; ".join(results)


@app.post("/settings/ais", response_class=PlainTextResponse)
def settings_ais(request: Request, aisstream_api_key: str = Form("")):
    htmx_only(request)
    from app.collectors import ais
    key = keep_or_new(aisstream_api_key, "aisstream_api_key")
    if not key:
        return "No key given."
    try:
        n = ais.probe(key)
    except Exception as e:
        return f"Key rejected: {e}"
    settings.set_values({"aisstream_api_key": key})
    return f"Saved. Stream works ({n} messages in 10 s). Ship layer starts within 5 minutes."


@app.post("/settings/smb", response_class=PlainTextResponse)
def settings_smb(request: Request, backup_smb_user: str = Form(""), backup_smb_pass: str = Form("")):
    htmx_only(request)
    settings.set_values({"backup_smb_user": backup_smb_user, "backup_smb_pass": keep_or_new(backup_smb_pass, "backup_smb_pass")})
    return "Saved."


@app.post("/settings/timezone", response_class=PlainTextResponse)
def settings_timezone(request: Request, timezone_name: str = Form("", alias="timezone")):
    htmx_only(request)
    from zoneinfo import ZoneInfo
    try:
        ZoneInfo(timezone_name.strip())
    except Exception:
        return "Unknown timezone name."
    settings.set_values({"timezone": timezone_name.strip()})
    return "Saved. Restart the service to apply."


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
