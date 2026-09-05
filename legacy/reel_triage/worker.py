from __future__ import annotations

import json
import os
import random
import subprocess
import tempfile
import time

from reel_triage import config, fetcher
from reel_triage.structurer import structure
from reel_triage.transcribe import transcribe


def _claim(conn):
    """One row at a time, oldest first; serial by design (no parallel fetch)."""
    cur = conn.cursor()
    return cur.execute(
        "select id, shortcode, url from reel_inbox "
        "where transcript is null and error is null and status = 'inbox' "
        "order by ingested_at limit 1").fetchone()


def process_one(conn) -> bool:
    row = _claim(conn)
    if not row:
        return False
    cur = conn.cursor()
    work = tempfile.mkdtemp(prefix="reel-")
    try:
        media = fetcher.fetch(row["url"], work)
        frames = fetcher.keyframes(media, os.path.join(work, "frames"))
        text = transcribe(media)
        fetcher.cleanup_media(media)  # media never persists
        data = structure(text, frames)
        cur.execute(
            "update reel_inbox set transcript=%s, topic=%s, claim=%s, evidence_grade=%s, "
            "action=%s, effort=%s, impact=%s, confidence=%s, priority=%s, raw_json=%s, "
            "status='triaged' where id=%s",
            (text, data["topic"], data["claim"], data["evidence_grade"], data["action"],
             data["effort"], data["impact"], data["confidence"], data["priority"],
             json.dumps(data), row["id"]))
        return True
    except subprocess.CalledProcessError as e:
        # FULL stderr — truncated evidence is not evidence (CAI-RESP-216)
        cur.execute("update reel_inbox set error=%s where id=%s",
                    (str(e.stderr or e), row["id"]))
        return True
    except Exception as e:  # noqa: BLE001 — any failure must land in error, not crash the loop
        cur.execute("update reel_inbox set error=%s where id=%s", (repr(e), row["id"]))
        return True


def _disabled() -> bool:
    return os.environ.get("WINGMEN_REEL_WORKER_DISABLED", "1").lower() in ("1", "true", "yes")


def run_forever(conn_factory):
    """Mac Studio launchd entrypoint. Fail-closed: boots disabled unless
    WINGMEN_REEL_WORKER_DISABLED is explicitly set to 0/false."""
    if _disabled():
        print("reel-worker DISABLED (set WINGMEN_REEL_WORKER_DISABLED=0 to enable)")
        return
    lo, hi = config.FETCH_SLEEP_RANGE
    while True:
        with conn_factory() as conn:
            did = process_one(conn)
        time.sleep(random.uniform(lo, hi) if did else hi)


if __name__ == "__main__":
    from reel_triage import db
    run_forever(db.connect)
