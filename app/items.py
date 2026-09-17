"""Shared helpers for article-like items: keyword tagging, dedup keys, storage."""
import hashlib
import re
from contextlib import closing
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from app import db
from app.config import SETTINGS

KEYWORDS = SETTINGS["keywords"]


def _pattern(terms: list[str]) -> re.Pattern:
    # Word boundaries for Latin terms, plain substring for CJK
    parts = [rf"\b{re.escape(t)}\b" if t.isascii() else re.escape(t) for t in terms]
    return re.compile("|".join(parts), re.IGNORECASE)


CONTEXT = _pattern(KEYWORDS["context"])
GROUPS = {name: _pattern(terms) for name, terms in KEYWORDS["groups"].items()}


def classify(text: str, context_implied: bool = False) -> tuple[list[str], bool]:
    tags = [name for name, pattern in GROUPS.items() if pattern.search(text)]
    in_context = context_implied or bool(CONTEXT.search(text))
    relevant = any(t in KEYWORDS["always"] for t in tags) or (bool(tags) and in_context)
    return tags, relevant


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith(("utm_", "fbclid", "gclid"))])
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), query, ""))


def title_hash(title: str) -> str:
    normalized = re.sub(r"[\W_]+", "", title.lower())
    return hashlib.sha1(normalized.encode()).hexdigest()


def clean_text(html: str, limit: int = 300) -> str:
    text = re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r"\s+", " ", text).strip()[:limit]


def save(source: str, url: str, title: str, published: datetime, snippet: str = "", context_implied: bool = False,
         dedup_key: str | None = None) -> bool:
    """Insert an item unless its canonical URL or title (or dedup_key) was seen before. Returns True if new."""
    title = clean_text(title, 500)
    tags, relevant = classify(f"{title} {snippet}", context_implied)
    with closing(db.connect()) as conn, conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO items (source, url, canonical_url, title, title_hash, published_utc, fetched_utc, snippet, tags, relevant) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (source, url, canonical_url(url), title, title_hash(dedup_key or title),
             published.astimezone(timezone.utc).isoformat(timespec="seconds"),
             datetime.now(timezone.utc).isoformat(timespec="seconds"),
             snippet, ",".join(tags), relevant),
        )
        return cur.rowcount == 1
