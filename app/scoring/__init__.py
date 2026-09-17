from app.scoring import index, tripwires


def run() -> str:
    """Recompute the index, then evaluate tripwires against today's scores."""
    summary = index.compute()
    return f"{summary}; tripwires {tripwires.evaluate()}"
