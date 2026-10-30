#!/usr/bin/env python3
"""live_checkout_branch_watchdog.py — pages orch-console if the LIVE checkout's branch
stays off fable/substrate-safe-fixes for more than GRACE_S (bus #44681), or if the LIVE
checkout's INDEX carries staged changes for more than GRACE_S (bus #85029).

WHY (branch): cc-angullia branched/committed/pushed from INSIDE ~/wingmen/orchestrator
(the live checkout; the commitment sweeper, DDL watchdog, and every other launchd daemon
execute from it every few minutes) and switched back. No damage that time (verified back
on fable), but for that window the daemons ran another branch's code.
scripts/git-hooks/post-checkout pages immediately on the checkout event itself; this
watchdog is the independent backstop for anything that changes HEAD without firing that
hook (a crashed/bypassed hook, `git symbolic-ref`, a detached-HEAD `git reset`) and for
confirming drift is SUSTAINED rather than a one-scan blip.

WHY (staged index, bus #85029): orch-console found the live checkout 8 commits behind
origin AND holding a STAGED REVERT of PR#681 (two files staged back to their pre-681
content, working tree matching) — the dual-worktree staged-revert signature. Console
restored both files by hand; a commit from the live checkout at that moment would have
silently reverted merged work with no branch-name signal at all (still on fable). The
branch check alone is blind to this class of contamination, so this is checked
independently, same grace/alert/dedup shape, not a variant of the branch logic.

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
import uuid
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
STAGED_SUBJECT_PREFIX = "[live-checkout-staged]"   # dedup anchor — keep STABLE, distinct from branch's


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


def staged_alert_message(staged_files, age_s):
    """PURE (subject, body) for the staged-index operator alert. staged_files is the
    non-empty list of paths `git diff --cached --name-only` reported."""
    n = len(staged_files)
    shown = ", ".join(staged_files[:8]) + (f", +{n - 8} more" if n > 8 else "")
    subject = f"{STAGED_SUBJECT_PREFIX} live checkout has {n} staged file(s) (>{age_s}s)"
    body = (
        f"TL;DR: the LIVE checkout ($HOME/wingmen/orchestrator) has had {n} staged "
        f"(git add'ed, not committed) file(s) for {age_s}s: {shown}\n\n"
        f"IMPACT: this checkout is what the commitment sweeper, DDL watchdog, and every "
        f"other launchd daemon execute from. A commit made from here right now would ship "
        f"whatever is staged — including, as happened 2026-10-30 (bus #85029), a staged "
        f"REVERT of already-merged work (PR#681) left over from a dual-worktree mixup, "
        f"with the branch itself showing no drift at all.\n"
        f"WHAT TO DO: git -C ~/wingmen/orchestrator diff --cached  (inspect what's staged), "
        f"then either git -C ~/wingmen/orchestrator restore --staged . (if it's unwanted) "
        f"or commit it from a proper worktree instead. Lane/PR work belongs in a "
        f"`git worktree add` checkout, never staged directly in the live one. "
        f"Detect-only: I did not touch the checkout or its index."
    )
    return subject, body


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


def _read_staged_files() -> "list[str] | None":
    """Paths currently staged in the LIVE checkout's index, or None on any read miss --
    fail-safe, never assert a verdict for state we could not actually observe. Empty
    list means a genuinely clean index, distinct from None (unreadable)."""
    if not LIVE_CHECKOUT.is_dir():
        return None
    try:
        r = subprocess.run(
            ["git", "-C", str(LIVE_CHECKOUT), "diff", "--cached", "--name-only"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return None
        return [p for p in r.stdout.splitlines() if p.strip()]
    except Exception:
        return None


def _alert(cur, conn, current_branch, age_s, dry: bool) -> str:
    subject, body = alert_message(current_branch, age_s)
    if dry:
        print(f"    WOULD-ALERT orch-console: {subject}")
        return "would-alert"
    # RPC on the EXISTING cursor/connection (backlog#124, bus #74939), not bus_send.send() --
    # keeps this on the one connection run() already holds, no need to churn a second one
    # for a single-shot alert.
    cur.execute(
        "SELECT * FROM post_agent_message('cc-fleet-health','orch-console','blocker',%s,%s,"
        "'P1',false,%s,%s,%s,%s)", [subject, body, str(uuid.uuid4()), None, False, None])
    conn.commit()
    print(f"    ALERTED orch-console: {subject}")
    return "alerted"


def _alert_staged(cur, conn, staged_files, age_s, dry: bool) -> str:
    subject, body = staged_alert_message(staged_files, age_s)
    if dry:
        print(f"    WOULD-ALERT orch-console: {subject}")
        return "would-alert"
    cur.execute(
        "SELECT * FROM post_agent_message('cc-fleet-health','orch-console','blocker',%s,%s,"
        "'P1',false,%s,%s,%s,%s)", [subject, body, str(uuid.uuid4()), None, False, None])
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
        return _run_staged(conn, state, now, dry)

    verdict, since = evaluate(branch, state.get("off_branch_since"), now)
    state["_heartbeat_epoch"] = now

    if verdict == "on_branch":
        if state.get("off_branch_since"):
            print(f"    back on {EXPECTED_BRANCH} — clearing episode")
        state.pop("off_branch_since", None)
        state.pop("last_alert_at", None)
        if not dry:
            _save_state(state)
        return _run_staged(conn, state, now, dry)

    state["off_branch_since"] = since
    age = now - since

    if verdict == "grace":
        print(f"    grace: off-branch {age}s (<{GRACE_S}s) — waiting to confirm sustained")
        if not dry:
            _save_state(state)
        return _run_staged(conn, state, now, dry)

    # verdict == "alert"
    last_alert = int(state.get("last_alert_at", 0))
    if (now - last_alert) < REPAGE_S:
        print(f"    still off-branch ({age}s) but re-page cooldown active — logged only")
        if not dry:
            _save_state(state)
        return _run_staged(conn, state, now, dry)

    with conn.cursor() as cur:
        result = _alert(cur, conn, branch, age, dry)
    if result == "alerted":
        state["last_alert_at"] = now
    if not dry:
        _save_state(state)
    return _run_staged(conn, state, now, dry)


def _run_staged(conn, state: dict, now: int, dry: bool) -> int:
    """Independent of the branch check above -- a clean branch says nothing about a
    dirty index (bus #85029: branch was fine, two files were staged). Shares the same
    state dict/file (separate keys) and the same evaluate() grace/alert state machine,
    keyed off "clean" vs "staged" instead of a branch name."""
    staged = _read_staged_files()
    print(f"    staged-index check — staged={staged!r}")

    if staged is None:
        print("    read-miss (checkout missing/unreadable) — fail-safe, no assert")
        if not dry:
            _save_state(state)
        return 0

    current = "staged" if staged else "clean"
    verdict, since = evaluate(current, state.get("staged_since"), now, expected="clean")

    if verdict == "on_branch":  # i.e. clean
        if state.get("staged_since"):
            print("    index clean again — clearing episode")
        state.pop("staged_since", None)
        state.pop("last_staged_alert_at", None)
        if not dry:
            _save_state(state)
        return 0

    state["staged_since"] = since
    age = now - since

    if verdict == "grace":
        print(f"    grace: staged {age}s (<{GRACE_S}s) — waiting to confirm sustained")
        if not dry:
            _save_state(state)
        return 0

    # verdict == "alert"
    last_alert = int(state.get("last_staged_alert_at", 0))
    if (now - last_alert) < REPAGE_S:
        print(f"    still staged ({age}s) but re-page cooldown active — logged only")
        if not dry:
            _save_state(state)
        return 0

    with conn.cursor() as cur:
        result = _alert_staged(cur, conn, staged, age, dry)
    if result == "alerted":
        state["last_staged_alert_at"] = now
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
