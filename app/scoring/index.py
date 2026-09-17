"""Baselines, z-scores, sub-scores and the composite 0-100 index."""
import json
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from statistics import mean, pstdev

from app import db
from app.config import SETTINGS
from app.scoring import indicators

CFG = SETTINGS["scoring"]
PRIORS = CFG["priors"]
# Indicators whose value is already on a sigma-like scale (no baseline)
DIRECT = {"advisory": 2.0}
WINDOWS = (30, 90)
HISTORY_DAYS = 90


def baseline(values: list[float], prior: tuple[float, float]) -> tuple[float, float]:
    """Blend observed mean/std with the prior; the prior fades as history accumulates."""
    if not values:
        return prior
    w = len(values) / (len(values) + CFG["prior_weight_days"])
    m = mean(values)
    s = pstdev(values) if len(values) > 1 else prior[1]
    return w * m + (1 - w) * prior[0], max(w * s + (1 - w) * prior[1], prior[1] * 0.5)


def z_scores(name: str, s: dict[str, float], day: str) -> dict | None:
    value = s.get(day)
    if value is None:
        return None
    if name in DIRECT:
        z = value * DIRECT[name]
        return {"value": value, "z30": z, "z90": z, "z": z}
    out = {"value": round(value, 2)}
    zs = []
    for window in WINDOWS:
        start = (date.fromisoformat(day) - timedelta(days=window)).isoformat()
        hist = [v for d, v in s.items() if start <= d < day]
        m, sd = baseline(hist, tuple(PRIORS[name]))
        z = (value - m) / sd
        out[f"z{window}"] = round(z, 2)
        zs.append(z)
    out["z"] = round(sum(zs) / len(zs), 2)
    return out


def subscore(weights: dict[str, float], zs: dict[str, dict]) -> float:
    signed = [(abs(w), (1 if w > 0 else -1) * zs[n]["z"]) for n, w in weights.items() if n in zs]
    if not signed:
        return 50.0
    # Weighted mean keeps the score stable; the max term lets one strong signal show through
    weighted = sum(w * z for w, z in signed) / sum(w for w, _ in signed)
    combined = 0.6 * weighted + 0.4 * max(z for _, z in signed)
    return max(0.0, min(100.0, 50 + CFG["points_per_sigma"] * combined))


def score_day(series: dict, day: str) -> dict:
    zs = {name: z for name, s in series.items() if (z := z_scores(name, s, day))}
    subs = {sub: subscore(weights, zs) for sub, weights in CFG["subscores"].items()}
    composite = sum(CFG["weights"][sub] * subs[sub] for sub in subs)
    return {"day": day, "composite": round(composite, 1), **{k: round(v, 1) for k, v in subs.items()}, "details": zs}


def compute() -> str:
    series = indicators.series(HISTORY_DAYS + max(WINDOWS))
    today = date.today()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(db.connect()) as conn, conn:
        for i in range(HISTORY_DAYS - 1, -1, -1):
            r = score_day(series, (today - timedelta(days=i)).isoformat())
            conn.execute(
                "INSERT OR REPLACE INTO scores (day, composite, military, economic, diplomatic, rhetoric, details, computed_utc) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (r["day"], r["composite"], r["military"], r["economic"], r["diplomatic"], r["rhetoric"],
                 json.dumps(r["details"]), now))
    return f"index {r['composite']} (mil {r['military']}, eco {r['economic']}, dip {r['diplomatic']}, rhe {r['rhetoric']})"
