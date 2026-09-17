import httpx

from app import settings


def send(text: str, token: str | None = None, chat_id: str | None = None) -> None:
    token = token or settings.get("telegram_bot_token")
    chat_id = chat_id or settings.get("telegram_chat_id")
    if not token or not chat_id:
        raise RuntimeError("Telegram not configured (Settings page)")
    r = httpx.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        timeout=20,
    )
    # Raise without the request URL so the bot token never reaches the logs
    if r.status_code != 200:
        raise RuntimeError(f"Telegram sendMessage failed: {r.status_code} {r.text[:200]}")


def get_me(token: str) -> dict:
    r = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"Telegram getMe failed: {r.status_code} {r.text[:200]}")
    return r.json()["result"]


def detect_chat(token: str) -> dict | None:
    """Chat of the most recent message sent to the bot, via getUpdates (server side, never a browser URL)."""
    r = httpx.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"Telegram getUpdates failed: {r.status_code} {r.text[:200]}")
    updates = [u for u in r.json().get("result", []) if u.get("message")]
    return updates[-1]["message"]["chat"] if updates else None
