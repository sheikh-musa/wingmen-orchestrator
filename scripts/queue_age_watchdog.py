#!/usr/bin/env python3
"""queue_age_watchdog.py — page on claimable dispatch-queue rows that age unclaimed.

Nazim #40835 (trigger op#20774: P1 rows sat 8.5h / 12h in coord_dispatch_queue with
ZERO pages). A CLAIMABLE row — claimed_by IS NULL (a hold:* row carries a non-null
claimed_by, so it is excluded by construction) and done_at IS NULL — that ages past its
per-priority floor (P1 > 30 min, P2 > 4 h; P3 never pages) pages the pool owner (hub,
cc-orchestrator) AND orch-console with the row id/title/age, then re-pages every 60 min
until it is claimed.

Same doctrine as the SRE's other nets: lease-gated ACTIONS (CAI-RESP-501; a non-holder
downgrades to detect-only), dead-man (fail LOUD, stamp state only on a confirmed page so a
failed send retries), and OBSERVE-FIRST — ships INERT (detect-only, log what it WOULD page)
until QUEUE_AGE_ALERT_ENABLED=1 arms it after a short soak. Detection + logging are ungated.

Runs once per launchd StartInterval (no internal loop).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ORCH = Path(os.path.expanduser("~/wingmen/orchestrator"))
sys.path.insert(0, str(ORCH))

from scripts.lib import fleet_health_lease  # noqa: E402

STATE_FILE = ORCH / "logs" / "queue_age_watchdog_state.json"
LOG_FILE = ORCH / "logs" / "queue_age_watchdog.log"


def _envint(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


# Per-priority age floors (minutes) before a claimable row pages.
P1_AFTER_MIN = _envint("QUEUE_AGE_P1_MIN", 30)
P2_AFTER_MIN = _envint("QUEUE_AGE_P2_MIN", 240)      # 4 h
REPAGE_EVERY_MIN = _envint("QUEUE_AGE_REPAGE_MIN", 60)
MAX_PAGES_PER_RUN = _envint("QUEUE_AGE_MAX_PAGES_PER_RUN", 5)

# Priorities eligible to page (P3 never pages). Floors keyed here.
PAGE_FLOORS = {"P1": P1_AFTER_MIN, "P2": P2_AFTER_MIN}

# Pool owner(s) to page: the hub owns the queue; orch-console voices the operator.
OWNERS = [a.strip() for a in os.environ.get(
    "QUEUE_AGE_OWNERS", "cc-orchestrator,orch-console").split(",") if a.strip()]

# Pool queues to scan. coord_dispatch_queue is the named target; message_queue /
# anchor_queue are NOT pool-claimable (TG buffer / retry queue) so they are out of scope.
# Allowlist-validated identifiers (no SQL injection); extend via env for a future queue.
QUEUE_TABLES = [t.strip() for t in os.environ.get(
    "QUEUE_AGE_TABLES", "coord_dispatch_queue").split(",") if t.strip()]

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


def log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat()} | {msg}"
    print(line)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _dsn() -> str:
    from dotenv import load_dotenv
    load_dotenv(str(ORCH / ".env"))
    return os.environ.get("SUPABASE_DB_URL") or os.environ["DATABASE_URL"]


def _connect():
    import psycopg
    return psycopg.connect(_dsn(), connect_timeout=15)


def load_state() -> dict:
    try:
        s = json.loads(STATE_FILE.read_text())
        if "pages" not in s:
            s["pages"] = {}
        return s
    except Exception:
        return {"pages": {}}


def save_state(state: dict) -> None:
    cutoff = time.time() - 7 * 86400          # drop page-stamps older than 7d
    state["pages"] = {k: v for k, v in state.get("pages", {}).items()
                      if isinstance(v, (int, float)) and v >= cutoff}
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=2))
    except Exception as e:
        log(f"save_state failed: {e!r}")


# ── pure selection predicate ────────────────────────────────────────────────
def queue_age_targets(rows, *, now, page_state, p1_after_min=P1_AFTER_MIN,
                      p2_after_min=P2_AFTER_MIN, repage_every_min=REPAGE_EVERY_MIN):
    """PURE: claimable rows (claimed_by None -> excludes claimed AND hold:* rows; done_at
    None) whose age >= the per-priority floor and which are due under the re-page cadence.
    Rows are dicts with id, priority, title, family, created_epoch, claimed_by, done_at.
    Each returned row is annotated with age_min + owners. P3 (and any non-P1/P2) never
    pages. `page_state` maps str(id) -> last-page epoch."""
    floors = {"P1": p1_after_min, "P2": p2_after_min}
    due = []
    for r in rows:
        if r.get("claimed_by") is not None:       # claimed, or held (hold:*) -> not claimable
            continue
        if r.get("done_at") is not None:
            continue
        pr = r.get("priority")
        if pr not in floors:                      # P3 / unknown -> never page
            continue
        age_min = int((now - float(r["created_epoch"])) / 60)
        if age_min < floors[pr]:
            continue
        last = page_state.get(str(r.get("id")), 0) or 0
        if (now - last) < repage_every_min * 60:  # re-page cadence
            continue
        t = dict(r)
        t["age_min"] = age_min
        t["owners"] = list(OWNERS)
        due.append(t)
    return due


# ── the action: page owners, capped, stamp-on-success-only (dead-man) ────────
def page_queue_owners(targets, *, dry, now, page_state, send_page,
                      max_pages=MAX_PAGES_PER_RUN):
    """Page each target's owners (hub + console). Capped per scan (defence vs a logic
    bug). DEAD-MAN: stamp the page timestamp ONLY on a confirmed successful send; a failed
    send stays unstamped so the next scan retries rather than silently suppressing for a
    full cadence. Dry-run sends nothing and stamps nothing. `send_page(target)->bool`."""
    sent = 0
    for t in targets:
        if sent >= max_pages:
            log(f"queue-age page HELD (per-scan cap {max_pages}) #{t.get('id')}")
            continue
        if dry:
            continue
        if send_page(t):
            page_state[str(t["id"])] = now
            sent += 1
        else:
            log(f"queue-age page SEND FAILED #{t.get('id')} (left UNSTAMPED for retry — dead-man)")
    return sent


# ── impure DB helpers ────────────────────────────────────────────────────────
def _fetch_claimable(conn, table: str = "coord_dispatch_queue"):
    if not _IDENT.match(table):
        raise ValueError(f"unsafe queue table identifier: {table!r}")
    cols = ["id", "title", "priority", "family", "created_epoch", "claimed_by", "done_at"]
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT id, title, priority, family, EXTRACT(epoch FROM created_at) AS created_epoch, "
            f"       claimed_by, done_at "
            f"FROM {table} WHERE claimed_by IS NULL AND done_at IS NULL")
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _send_queue_page(conn, target: dict) -> bool:
    """Post ONE page to EACH owner (hub + console). Returns True only if ALL owner rows
    committed; any failure -> False (unstamped -> retry). Non-rr alert; the owner acts by
    claiming/reassigning/holding the row."""
    tid = target.get("id")
    pr = target.get("priority")
    age = target.get("age_min")
    floor = PAGE_FLOORS.get(pr, "?")
    subj = f"[queue-age] {pr} coord_dispatch #{tid} UNCLAIMED ~{age}m — claim, reassign, or hold it"
    body = (
        f"TL;DR: dispatch-queue row #{tid} ({pr}, family {target.get('family')}) has sat CLAIMABLE and "
        f"unclaimed ~{age}m (floor {floor}m). Title: {target.get('title')}. "
        f"Claim it, reassign it, or mark it hold:<reason> if it should not be pool-dispatched. "
        f"Re-pages every {REPAGE_EVERY_MIN}m until claimed. (Closes the op#20774 gap: rows sat 8.5-12h unpaged.)")
    try:
        with conn.cursor() as cur:
            for owner in target.get("owners", OWNERS):
                cur.execute(
                    "INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,"
                    "  requires_response,priority,is_test) "
                    "VALUES ('cc-fleet-health',%s,'escalation',%s,%s,false,%s,false)",
                    (owner, subj, body, pr))
        conn.commit()
        log(f"queue-age PAGED #{tid} ({pr}, {age}m) -> {','.join(target.get('owners', OWNERS))}")
        return True
    except Exception as e:
        log(f"queue-age page INSERT failed #{tid}: {e!r}")
        try:
            conn.rollback()
        except Exception:
            pass
        return False


# ── orchestration ────────────────────────────────────────────────────────────
def run(dry: bool = False) -> int:
    persist = not dry
    state = load_state()
    now = time.time()
    all_targets: list = []
    try:
        conn = _connect()
    except Exception as e:
        log(f"db-connect-failed: {e!r}")
        return 1
    try:
        # OBSERVE-FIRST: ships INERT (detect-only, log what it WOULD page) until armed.
        alert_armed = os.environ.get("QUEUE_AGE_ALERT_ENABLED", "0") == "1"
        page_dry = dry or not alert_armed
        if not page_dry:
            # Pen gate (CAI-RESP-501): only the fleet_health_lease holder pages; a known
            # non-holder downgrades to detect-only (never double-page a reclaiming hub).
            ok, why = fleet_health_lease.gate()
            if not ok:
                log(f"pen-gate: queue-age DEFERRED (CAI-RESP-501) — {why}; detect-only this scan")
                page_dry = True
        page_state = state.setdefault("pages", {})
        claimable = 0
        for table in QUEUE_TABLES:
            try:
                fetched = _fetch_claimable(conn, table)
                claimable += len(fetched)
                all_targets += queue_age_targets(fetched, now=now, page_state=page_state)
            except Exception as e:                 # fail LOUD, keep scanning other tables
                log(f"queue-age fetch/predicate ERROR ({table}): {e!r}")
        n = 0
        if all_targets:
            n = page_queue_owners(all_targets, dry=page_dry, now=now, page_state=page_state,
                                  send_page=lambda t: _send_queue_page(conn, t))
        # Heartbeat: log EVERY scan (even when clean) so a detect-only soak is observable —
        # a net that only speaks on findings is indistinguishable from a dead one.
        detail = "".join(f" #{t['id']}({t['priority']},{t['age_min']}m)" for t in all_targets)
        log(f"queue-age scan [{'DETECT-ONLY' if page_dry else 'ARMED'}]: {claimable} claimable, "
            f"{len(all_targets)} over-age{',' + (' would-page' if page_dry else f' paged {n}') if all_targets else ''}"
            f"{detail}")
        state["last_scan"] = now
        if persist:
            save_state(state)
    finally:
        try:
            conn.close()
        except Exception:
            pass
    if dry:
        print(f"\n=== DRY-RUN: {len(all_targets)} over-age claimable row(s) "
              f"(P1>{P1_AFTER_MIN}m, P2>{P2_AFTER_MIN}m) ===")
        if not all_targets:
            print("(none)")
        for t in all_targets:
            print(f"  #{t['id']} {t['priority']} age={t['age_min']}m -> {','.join(t['owners'])}  "
                  f"{(t.get('title') or '')[:60]}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="scan + print what WOULD page; no bus writes, no state changes")
    args = ap.parse_args()
    return run(dry=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
