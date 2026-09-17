"""Free-tier LLM chain: Gemini, then Groq, then OpenRouter. JSON in, JSON out, with a daily call cap."""
import json
import logging
import os
import re
from contextlib import closing
from datetime import datetime, timezone

import httpx

from app import db
from app.config import LOCAL_TZ, SETTINGS

log = logging.getLogger("uvicorn.error")
CFG = SETTINGS["llm"]
TIMEOUT = 90


class CapReached(Exception):
    pass


def calls_today() -> int:
    start = datetime.now(LOCAL_TZ).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    with closing(db.connect()) as conn:
        return conn.execute("SELECT COUNT(*) FROM llm_calls WHERE ts_utc >= ?", (start.isoformat(timespec="seconds"),)).fetchone()[0]


def record(provider: str, model: str, purpose: str, ok: bool, error: str | None = None) -> None:
    with closing(db.connect()) as conn, conn:
        conn.execute("INSERT INTO llm_calls (ts_utc, provider, model, purpose, ok, error) VALUES (?, ?, ?, ?, ?, ?)",
                     (datetime.now(timezone.utc).isoformat(timespec="seconds"), provider, model, purpose, ok, error))


def gemini(key: str, model: str, system: str, user: str, json_mode: bool) -> str:
    body = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"temperature": 0.2, **({"responseMimeType": "application/json"} if json_mode else {})},
    }
    r = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                   params={"key": key}, json=body, timeout=TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def openai_compatible(url: str):
    def call(key: str, model: str, system: str, user: str, json_mode: bool) -> str:
        body = {"model": model, "temperature": 0.2,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        r = httpx.post(url, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=TIMEOUT)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
        return r.json()["choices"][0]["message"]["content"]
    return call


CALLERS = {
    "gemini": gemini,
    "groq": openai_compatible("https://api.groq.com/openai/v1/chat/completions"),
    "openrouter": openai_compatible("https://openrouter.ai/api/v1/chat/completions"),
}


def parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1])


def complete(purpose: str, system: str, user: str, json_mode: bool = True):
    """Return (result, provider). Tries each configured provider in order; raises when all fail or the cap is hit."""
    if calls_today() >= CFG["daily_cap"]:
        raise CapReached(f"daily cap of {CFG['daily_cap']} LLM calls reached")
    errors = []
    for p in CFG["providers"]:
        key = os.environ.get(f"{p['name'].upper()}_API_KEY", "")
        if not key:
            continue
        try:
            text = CALLERS[p["name"]](key, p["model"], system, user, json_mode)
            result = parse_json(text) if json_mode else text.strip()
        except Exception as e:
            record(p["name"], p["model"], purpose, False, repr(e)[:300])
            errors.append(f"{p['name']}: {e!r}"[:160])
            continue
        record(p["name"], p["model"], purpose, True)
        return result, p["name"]
    raise RuntimeError("all providers failed: " + " | ".join(errors))
