"""Batch analysis of relevant items and translation of MSA warning titles."""
import json
import re
from contextlib import closing
from datetime import datetime, timedelta, timezone

from app import db
from app.config import SETTINGS
from app.llm import chain
from app.web.data import rows

CFG = SETTINGS["llm"]
CJK = re.compile(r"[぀-ヿ㐀-鿿]")

SYSTEM_ANALYZE = """You analyze news items and official notices about China, Taiwan and US tension in the Taiwan Strait for an early-warning system.
Return JSON only, shaped as {"items": [{"id": <int>, "category": "military|economic|diplomatic|rhetoric|other", "severity": 1-5, "physical": true|false, "novel": true|false, "title_en": "<string or null>", "summary": "<one sentence>"}]}.
severity: 1 routine or unrelated, 2 notable, 3 clear escalation signal, 4 serious escalation, 5 imminent conflict, blockade, quarantine or evacuation.
physical: true when the item reports a concrete action (military movement, exercise, incursion, incident, sanction enacted, evacuation), false for statements, threats, analysis or opinion.
novel: false when the item repeats an already known, ongoing story.
title_en: English translation of the title when the title is not in English, otherwise null.
Return one entry per input id."""

SYSTEM_TRANSLATE_ITEMS = """Translate each news or notice title (Chinese or Japanese) into concise English. Keep names and numbers.
Return JSON only: {"translations": [{"i": <index>, "en": "<English>"}]}."""

SYSTEM_TRANSLATE = """Translate each Chinese maritime navigation warning title into concise English. Keep warning numbers as they are.
"X航警 N/26" means "X Navigation Warning N/26" where 闽 = Fujian, 浙 = Zhejiang, 沪 = Shanghai, 粤 = Guangdong, 鲁 = Shandong. 实弹射击 = live-fire exercise, 军事演习 = military exercise.
Return JSON only: {"translations": [{"i": <index>, "en": "<English>"}]}."""


def analyze_items() -> str:
    since = (datetime.now(timezone.utc) - timedelta(days=CFG["max_item_age_days"])).isoformat(timespec="seconds")
    items = rows("SELECT id, source, title, snippet FROM items WHERE relevant = 1 AND published_utc >= ? "
                 "AND id NOT IN (SELECT item_id FROM item_analysis) ORDER BY published_utc DESC LIMIT ?", since, CFG["batch_size"])
    if not items:
        return "no new items"
    payload = [{"id": i["id"], "source": i["source"], "title": i["title"], "snippet": (i["snippet"] or "")[:200]} for i in items]
    result, provider = chain.complete("analyze", SYSTEM_ANALYZE, json.dumps(payload, ensure_ascii=False))
    by_id = {int(r["id"]): r for r in result.get("items", []) if "id" in r}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(db.connect()) as conn, conn:
        for it in items:
            r = by_id.get(it["id"])
            # Items the model skipped are stored as routine so they are not resent every run
            title_en = (r or {}).get("title_en") or None
            conn.execute(
                "INSERT OR IGNORE INTO item_analysis (item_id, category, severity, physical, novel, title_en, summary, provider, analyzed_utc) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (it["id"], (r or {}).get("category", "other"), int((r or {}).get("severity", 1)), bool((r or {}).get("physical")),
                 bool((r or {}).get("novel", True)), title_en, (r or {}).get("summary"), provider, now))
            if title_en:
                conn.execute("UPDATE items SET title_en = ? WHERE id = ?", (title_en, it["id"]))
    return f"{len(items)} items analyzed ({provider}), {len(items) - len(by_id)} skipped"


def translate_items() -> str:
    """Non-English titles that analysis did not cover (non-relevant notices, older items)."""
    since = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat(timespec="seconds")
    candidates = rows("SELECT id, title FROM items WHERE title_en IS NULL AND published_utc >= ? ORDER BY published_utc DESC", since)
    todo = [c for c in candidates if CJK.search(c["title"])][:40]
    if not todo:
        return "no titles to translate"
    result, provider = chain.complete("translate", SYSTEM_TRANSLATE_ITEMS, json.dumps([{"i": i, "title": c["title"]} for i, c in enumerate(todo)], ensure_ascii=False))
    done = 0
    with closing(db.connect()) as conn, conn:
        for t in result.get("translations", []):
            try:
                item = todo[int(t["i"])]
            except (KeyError, ValueError, IndexError):
                continue
            if t.get("en"):
                conn.execute("UPDATE items SET title_en = ? WHERE id = ?", (str(t["en"])[:300], item["id"]))
                done += 1
    return f"{done} titles translated ({provider})"


def translate_msa() -> str:
    warnings = rows("SELECT url, title FROM msa_warnings WHERE military = 1 AND title_en IS NULL ORDER BY issued_date DESC LIMIT 40")
    if not warnings:
        return "no warnings to translate"
    payload = [{"i": i, "zh": w["title"]} for i, w in enumerate(warnings)]
    result, provider = chain.complete("translate", SYSTEM_TRANSLATE, json.dumps(payload, ensure_ascii=False))
    done = 0
    with closing(db.connect()) as conn, conn:
        for t in result.get("translations", []):
            try:
                w = warnings[int(t["i"])]
            except (KeyError, ValueError, IndexError):
                continue
            conn.execute("UPDATE msa_warnings SET title_en = ? WHERE url = ?", (str(t.get("en", ""))[:300], w["url"]))
            done += 1
    return f"{done} warnings translated ({provider})"


def run() -> str:
    # Each step is its own LLM call; one failing must not block the others
    parts, failed = [], False
    for step in (analyze_items, translate_items, translate_msa):
        try:
            parts.append(step())
        except Exception as e:
            parts.append(f"{step.__name__} failed: {e!r}"[:200])
            failed = True
    summary = "; ".join(parts)
    if failed:
        raise RuntimeError(summary)
    return summary
