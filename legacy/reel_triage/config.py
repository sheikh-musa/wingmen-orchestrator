import os

WIP_CAP = 3
MAX_KEYFRAMES = 6
FETCH_SLEEP_RANGE = (30, 60)
AUTO_DISCARD_AFTER_DIGESTS = 2
DIGEST_TOP_N = 5

_EFFORT_WEIGHTS = {"5min": 1, "habit": 2, "project": 4}


def effort_weight(effort: str) -> int:
    return _EFFORT_WEIGHTS[effort]


def reel_triage_enabled() -> bool:
    return os.environ.get("WINGMEN_REEL_TRIAGE_ENABLED", "").lower() in ("1", "true", "yes")


def reel_inbox_dsn() -> str:
    dsn = os.environ.get("REEL_INBOX_DB_URL")
    if not dsn:
        raise RuntimeError("REEL_INBOX_DB_URL not set (project tscuymavysscrvoberrr)")
    return dsn
