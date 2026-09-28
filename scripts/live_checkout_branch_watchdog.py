#!/usr/bin/env python3
"""live_checkout_branch_watchdog.py — pages orch-console if the LIVE checkout's branch
stays off fable/substrate-safe-fixes for more than GRACE_S (bus #44681).

WHY: cc-angullia branched/committed/pushed from INSIDE ~/wingmen/orchestrator (the live
checkout; the commitment sweeper, DDL watchdog, and every other launchd daemon execute
from it every few minutes) and switched back. No damage that time (verified back on
fable), but for that window the daemons ran another branch's code.
scripts/git-hooks/post-checkout pages immediately on the checkout event itself; this
watchdog is the independent backstop for anything that changes HEAD without firing that
hook (a crashed/bypassed hook, `git symbolic-ref`, a detached-HEAD `git reset`) and for
confirming drift is SUSTAINED rather than a one-scan blip.

GATING (CAI-501 charter §3 shape): DETECT + the operator alert are UNGATED — a safety
page is never silenced by lease state. Ops-only: own agent_id (cc-fleet-health, already
registered), never a governance write.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

_ORCH_DIR = Path(__file__).resolve().parent.parent
if str(_ORCH_DIR) not in sys.path:
    sys.path.insert(0, str(_ORCH_DIR))

LIVE_CHECKOUT = Path(os.environ.get("LIVE_CHECKOUT_PATH", str(Path.home() / "wingmen" / "orchestrator")))
EXPECTED_BRANCH = "fable/substrate-safe-fixes"
GRACE_S = int(os.environ.get("LIVE_CHECKOUT_GRACE_S", "120"))          # > 2 min sustained drift before paging
REPAGE_S = int(os.environ.get("LIVE_CHECKOUT_REPAGE_S", str(60 * 60)))  # don't re-page more than hourly
STATE_PATH = _ORCH_DIR / "state" / "live_checkout_branch_watchdog.json"
SUBJECT_PREFIX = "[live-checkout-branch]"   # dedup anchor — keep STABLE


# ── pure decision core (unit-tested; touches nothing) ────────────────────────
def evaluate(current_branch, off_branch_since, now, expected=EXPECTED_BRANCH, grace_s=GRACE_S):
    """PURE. (current_branch, off_branch_since epoch|None, now epoch) -> (verdict, new_off_branch_since).

    verdict in {"on_branch", "grace", "alert"}.
      on_branch — current_branch == expected: healthy, caller should clear tracking.
      grace     — off-branch but the sustained window is under grace_s: seen, not yet acted on.
      alert     — off-branch and sustained >= grace_s: page.
    new_off_branch_since is what the caller should persist as state (None when on_branch).
    off_branch_since=None on first detection starts the clock at `now` (age 0), so a single
    scan never alerts — grace_s must elapse across scans first.
    """
    if current_branch == expected:
        return "on_branch", None
    since = off_branch_since if off_branch_since is not None else now
    age = now - since
    if age >= grace_s:
        return "alert", since
    return "grace", since


def alert_message(current_branch, age_s, expected=EXPECTED_BRANCH):
    """PURE (subject, body) for the operator alert."""
    subject = f"{SUBJECT_PREFIX} live checkout on '{current_branch}', not '{expected}' (>{age_s}s)"
    body = (
        f"TL;DR: the LIVE checkout ($HOME/wingmen/orchestrator) has been on branch "
        f"'{current_branch}' for {age_s}s — expected '{expected}'.\n\n"
        f"IMPACT: the commitment sweeper, DDL watchdog, and every other launchd daemon that "
        f"executes from this checkout are running '{current_branch}' code, not the reviewed "
        f"trunk, until it is put back.\n"
        f"WHAT TO DO: git -C ~/wingmen/orchestrator checkout {expected}\n"
        f"Lane/PR work belongs in a `git worktree add` checkout, never inside the live one "
        f"(bus #44681). Detect-only: I did not touch the checkout."
    )
    return subject, body


# ── DB / state / paging shell (never touches the checkout; only detects + pages) ─────
def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.replace(STATE_PATH)  # atomic


def _read_branch() -> "str | None":
    """Current branch of the LIVE checkout, or None on any read miss — fail-safe, never
    assert a verdict for state we could not actually observe."""
    if not LIVE_CHECKOUT.is_dir():
        return None
    try:
        r = subprocess.run(
            ["git", "-C", str(LIVE_CHECKOUT), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return None
        return r.stdout.strip() or None
    except Exception:
        return None


def _alert(cur, conn, current_branch, age_s, dry: bool) -> str:
    subject, body = alert_message(current_branch, age_s)
    if dry:
        print(f"    WOULD-ALERT orch-console: {subject}")
        return "would-alert"
    cur.execute(
        "INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,priority,"
        " requires_response,created_at) VALUES "
        "('cc-fleet-health','orch-console','blocker',%s,%s,'P1',false, now())", [subject, body])
    conn.commit()
    print(f"    ALERTED orch-console: {subject}")
    return "alerted"


def run(conn, dry: bool = False) -> int:
    state = _load_state()
    now = int(time.time())
    branch = _read_branch()
    print(f"live-checkout-branch-watchdog — {'DRY-RUN' if dry else 'LIVE'} — "
          f"checkout={LIVE_CHECKOUT} expected={EXPECTED_BRANCH} grace={GRACE_S}s — branch={branch!r}")

    if branch is None:
        print("    read-miss (checkout missing/unreadable) — fail-safe, no assert")
        state["_heartbeat_epoch"] = now
        if not dry:
            _save_state(state)
        return 0

    verdict, since = evaluate(branch, state.get("off_branch_since"), now)
    state["_heartbeat_epoch"] = now

    if verdict == "on_branch":
        if state.get("off_branch_since"):
            print(f"    back on {EXPECTED_BRANCH} — clearing episode")
        state.pop("off_branch_since", None)
        state.pop("last_alert_at", None)
        if not dry:
            _save_state(state)
        return 0

    state["off_branch_since"] = since
    age = now - since

    if verdict == "grace":
        print(f"    grace: off-branch {age}s (<{GRACE_S}s) — waiting to confirm sustained")
        if not dry:
            _save_state(state)
        return 0

    # verdict == "alert"
    last_alert = int(state.get("last_alert_at", 0))
    if (now - last_alert) < REPAGE_S:
        print(f"    still off-branch ({age}s) but re-page cooldown active — logged only")
        if not dry:
            _save_state(state)
        return 0

    with conn.cursor() as cur:
        result = _alert(cur, conn, branch, age, dry)
    if result == "alerted":
        state["last_alert_at"] = now
    if not dry:
        _save_state(state)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="live-checkout branch-drift watchdog (bus #44681)")
    ap.add_argument("--dry-run", action="store_true", help="detect + log only; no alert, no state write")
    args = ap.parse_args()
    import psycopg
    from dotenv import load_dotenv
    load_dotenv(_ORCH_DIR / ".env")
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("no DATABASE_URL", file=sys.stderr)
        return 2
    conn = psycopg.connect(dsn)
    try:
        return run(conn, dry=args.dry_run)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
