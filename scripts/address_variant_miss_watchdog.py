#!/usr/bin/env python3
"""address_variant_miss_watchdog.py — catch a lane missing mail on its OWN
instance id while it is demonstrably alive and reading its base id.

WHY (bus #51166, 2026-10-04): cc-shipforge (#51066) and cc-scholar both
booted with their inbox reader filtering `to_agent=eq.<base>` only, silently
missing every message addressed to `<base>-<n>` (their instance/sub-tag id).
Scholar sat idle ~30 min on a GO it never saw. The boot-context root cause is
fixed in build_launch_context.py (same bus thread), but a lane's OWN runtime
reader can still regress the same way (a hand-rolled query, a stale copy of
the pattern, a new lane template drift). This is the standing detector for
that regression class — the precise signature orch-console asked for:

    a lane has unread agent_messages addressed to its INSTANCE id, older
    than UNREAD_MIN_AGE_MIN, WHILE it has a message addressed to its BASE id
    read within the last BASE_READ_RECENCY_MIN — i.e. it is provably awake
    and draining its base inbox, just blind to its own instance address.

Detect-only by default (--dry-run implied unless --page is passed). A page
is an agent_messages row to orch-console, deduped per instance via a JSON
state file (re-page at most once per PAGE_COOLDOWN_MIN while the condition
persists) — never a silent monitor (CLAUDE.md dead-man's-switch discipline).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ORCH = Path(os.path.expanduser("~/wingmen/orchestrator"))
STATE_FILE = ORCH / "logs" / "address_variant_miss_watchdog_state.json"
LOG_FILE = ORCH / "logs" / "address_variant_miss_watchdog.log"

UNREAD_MIN_AGE_MIN = int(os.environ.get("AVM_UNREAD_MIN_AGE_MIN", "20"))
BASE_READ_RECENCY_MIN = int(os.environ.get("AVM_BASE_READ_RECENCY_MIN", "20"))
PAGE_COOLDOWN_MIN = int(os.environ.get("AVM_PAGE_COOLDOWN_MIN", "240"))
PAGE_MESSAGE_TYPE = "blocker"


def log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat()} | {msg}"
    print(line)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def save_state(state: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state))
    except Exception as e:
        log(f"state-write-failed: {e}")


QUERY = """
WITH instances AS (
    SELECT DISTINCT agent_id AS instance_id, base_agent_id, status AS instance_status
    FROM agent_status
    WHERE base_agent_id IS NOT NULL AND agent_id <> base_agent_id
),
unread_instance AS (
    SELECT to_agent AS instance_id,
           MIN(created_at) AS oldest_unread_created_at,
           COUNT(*) AS unread_count
    FROM agent_messages
    WHERE read_at IS NULL
    GROUP BY to_agent
),
recent_base_read AS (
    SELECT to_agent AS base_id, MAX(read_at) AS last_base_read_at
    FROM agent_messages
    WHERE read_at IS NOT NULL
    GROUP BY to_agent
)
SELECT i.instance_id, i.base_agent_id, i.instance_status,
       u.oldest_unread_created_at, u.unread_count,
       r.last_base_read_at
FROM instances i
JOIN unread_instance u ON u.instance_id = i.instance_id
LEFT JOIN recent_base_read r ON r.base_id = i.base_agent_id
"""


def find_address_variant_misses(
    rows: list[dict],
    *,
    now: datetime,
    unread_min_age_min: int = UNREAD_MIN_AGE_MIN,
    base_read_recency_min: int = BASE_READ_RECENCY_MIN,
) -> list[dict]:
    """PURE: rows -> the subset that are a genuine address-variant miss.

    A row qualifies only when ALL hold: its oldest unread-on-instance row is
    older than unread_min_age_min (not a race with mail that just landed),
    its base id was read within base_read_recency_min (proof the lane FAMILY
    is awake and draining mail, not just generically idle/dead — that is a
    different, already-covered failure class), AND the instance itself is
    not offline (cc-quality #51199: a base being awake does not prove THIS
    specific instance still exists — a retired instance with old unread
    mail would otherwise be misdiagnosed as "awake but blind" when it is
    simply dead; checked separately from base liveness on purpose, since a
    base can outlive any one of its past instances)."""
    misses = []
    for r in rows:
        if r.get("instance_status") == "offline":
            continue
        oldest = r.get("oldest_unread_created_at")
        if oldest is None:
            continue
        unread_age_min = (now - oldest).total_seconds() / 60.0
        if unread_age_min < unread_min_age_min:
            continue
        last_base_read = r.get("last_base_read_at")
        if last_base_read is None:
            continue
        base_read_age_min = (now - last_base_read).total_seconds() / 60.0
        if base_read_age_min > base_read_recency_min:
            continue
        misses.append(r)
    return misses


def due_for_page(instance_id: str, state: dict, now_ts: float, cooldown_min: int = PAGE_COOLDOWN_MIN) -> bool:
    last = state.get(instance_id, {}).get("last_paged_ts")
    if last is None:
        return True
    return (now_ts - last) >= cooldown_min * 60


def _dsn() -> str:
    from dotenv import load_dotenv
    load_dotenv(str(ORCH / ".env"))
    return os.environ.get("SUPABASE_DB_URL") or os.environ["DATABASE_URL"]


def _fetch_rows(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(QUERY)
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _page(conn, miss: dict) -> bool:
    instance_id = miss["instance_id"]
    base_id = miss["base_agent_id"]
    subj = f"address-variant miss: {instance_id} blind to its own inbox"
    body = (
        f"TL;DR: {instance_id} has {miss['unread_count']} unread message(s) addressed "
        f"to its instance id, oldest since {miss['oldest_unread_created_at']}, while it "
        f"read mail on its base id {base_id} as recently as {miss['last_base_read_at']} — "
        f"it is awake and draining its inbox but blind to its own instance address "
        f"(bus #51166 regression class). Check its inbox reader filters on "
        f"to_agent IN (base, instance), not base alone."
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_agent_id','cc-fleet-health',true)")
            cur.execute(
                "INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,"
                "  requires_response,priority,is_test) "
                "VALUES ('cc-fleet-health','orch-console',%s,%s,%s,false,'P2',false)",
                (PAGE_MESSAGE_TYPE, subj, body),
            )
        conn.commit()
        log(f"PAGED {instance_id} (base={base_id}, unread={miss['unread_count']})")
        return True
    except Exception as e:
        log(f"page-failed {instance_id}: {e!r}")
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", default=True,
                     help="detect only, print findings, never page (default)")
    ap.add_argument("--page", dest="dry_run", action="store_false",
                     help="actually page orch-console for due findings")
    args = ap.parse_args(argv)

    import psycopg
    conn = psycopg.connect(_dsn(), connect_timeout=15)
    try:
        rows = _fetch_rows(conn)
    finally:
        if args.dry_run:
            conn.close()

    now = datetime.now(timezone.utc)
    misses = find_address_variant_misses(rows, now=now)

    if not misses:
        log("clean: no address-variant misses")
        return 0

    state = load_state()
    now_ts = time.time()
    for m in misses:
        iid = m["instance_id"]
        due = due_for_page(iid, state, now_ts)
        tag = "[DRY]" if args.dry_run else ("[DUE]" if due else "[COOLDOWN]")
        log(f"{tag} {iid} base={m['base_agent_id']} unread={m['unread_count']} "
            f"oldest={m['oldest_unread_created_at']} base_read={m['last_base_read_at']}")
        if args.dry_run or not due:
            continue
        if _page(conn, m):
            state.setdefault(iid, {})["last_paged_ts"] = now_ts

    if not args.dry_run:
        save_state(state)
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
