"""reset_auditor_arm_verify — verify + single-use-consume an arm-row for
reset_auditor.sh's console-executed self-reset path (Musa op_msg 35891,
ask 5849; orch-console decision #83292).

SCOPE: cc-quality ONLY (storefront/finance keep the existing tty-only
caller-identity path, CAI-RESP-1442 -- this is deliberately NOT a general
replacement; see CAI-RESP-1451 for why the tty gate is "unsatisfiable" for
an agent process with no real controlling terminal of its own to share).
This script re-checks the target independently of reset_auditor.sh's own
gate ordering (defense in depth, same style as the rest of the reset
family) -- it refuses any target other than "quality" even if called.

THE MARKER (document this exactly -- it's the only thing cc-quality needs
to know to post a valid request): message_type='decision' AND subject
starting with the literal prefix "SELF-RESET-REQUEST: cc-quality" (case-
sensitive, grep-able). Both conditions are required together so an
unrelated 'decision' row from cc-quality never accidentally qualifies.

TWO MODES, split on orch-console's own ordering correction (#83304): a bare
UPDATE...RETURNING right at the caller-identity gate would consume a
genuine request even when a LATER gate (busy, stale handoff, queued
composer) refuses the run, or even on RESET_DRYRUN -- burning a real
request for a run that never clears anything.
  --mode check    read-only (SELECT, same predicate, no write) -- used at
                  the caller-identity gate (section 3) to decide pass/fail
                  early, including under RESET_DRYRUN, without consuming.
  --mode consume  the atomic single-use UPDATE...RETURNING -- called ONLY
                  after every other gate (armed included) has already
                  passed, immediately before the pre-clear audit row.
                  Zero rows back here means refuse, even though check
                  passed earlier (closes the check-to-fire race window).
"Consumed" reuses responded_at (no new column/migration needed): console
"responding" to cc-quality's own request by acting on it is the correct,
not-overloaded, meaning of that column for a requires-response row
addressed to it. Every accidental way responded_at could get stamped
fails CLOSED (the row just has to be re-posted) -- so NOBODY may reply-to
or otherwise stamp responded_at on an arm row before reset_auditor.sh
itself fires it, orch-console included (console #83304).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import psycopg

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
from substrate_dsn import dsn_from_env_file  # noqa: E402

MARKER_PREFIX = "SELF-RESET-REQUEST: cc-quality"
MAX_AGE_SECONDS = 30 * 60  # 30 minutes (console spec)
_SCOPED_TARGETS = {"quality"}  # cc-quality ONLY -- storefront/finance never reach this

_WHERE_CLAUSE = """
  id = %(id)s
  AND from_agent = 'cc-quality'
  AND to_agent = 'orch-console'
  AND message_type = 'decision'
  AND subject LIKE %(marker)s
  AND responded_at IS NULL
  AND created_at >= now() - interval '30 minutes'
"""

_CHECK_SQL = f"SELECT id, created_at FROM agent_messages WHERE {_WHERE_CLAUSE}"
_CONSUME_SQL = f"UPDATE agent_messages SET responded_at = now() WHERE {_WHERE_CLAUSE} RETURNING id, created_at"

# Diagnostic-only (never the authority) -- so a refusal can say WHY.
_DIAGNOSE_SQL = "SELECT from_agent, to_agent, message_type, subject, created_at, responded_at FROM agent_messages WHERE id = %(id)s"


def _diagnose(cur, row_id: int) -> str:
    cur.execute(_DIAGNOSE_SQL, {"id": row_id})
    row = cur.fetchone()
    if row is None:
        return f"arm-row {row_id} does not exist"
    from_agent, to_agent, mtype, subject, created_at, responded_at = row
    if from_agent != "cc-quality":
        return f"arm-row {row_id} is from '{from_agent}', not cc-quality"
    if to_agent != "orch-console":
        return f"arm-row {row_id} is addressed to '{to_agent}', not orch-console"
    if mtype != "decision" or not (subject or "").startswith(MARKER_PREFIX):
        return f"arm-row {row_id} does not carry the SELF-RESET-REQUEST marker"
    if responded_at is not None:
        return f"arm-row {row_id} was already consumed at {responded_at} (single-use; cannot replay)"
    return f"arm-row {row_id} is older than {MAX_AGE_SECONDS}s (created {created_at})"


def check(conn, row_id: int, target: str) -> "tuple[bool, str]":
    """Read-only (no write) -- safe to call under RESET_DRYRUN and at the
    caller-identity gate, before the busy/handoff/queued/armed gates have
    even run. Never consumes."""
    if target not in _SCOPED_TARGETS:
        return False, f"arm-row path is scoped to cc-quality only, not '{target}'"
    with conn.cursor() as cur:
        cur.execute(_CHECK_SQL, {"id": row_id, "marker": f"{MARKER_PREFIX}%"})
        row = cur.fetchone()
        if row is None:
            reason = _diagnose(cur, row_id)
            conn.rollback()
            return False, reason
        conn.rollback()  # defensive -- this path never writes, nothing to commit
        return True, f"arm-row {row_id} checks out (created {row[1]})"


def consume(conn, row_id: int, target: str) -> "tuple[bool, str]":
    """The atomic single-use UPDATE...RETURNING. Call ONLY after every other
    gate (armed included) has passed, immediately before the pre-clear audit
    row -- zero rows back here refuses the run even if check() passed earlier
    (closes the check-to-fire race window)."""
    if target not in _SCOPED_TARGETS:
        return False, f"arm-row path is scoped to cc-quality only, not '{target}'"
    with conn.cursor() as cur:
        cur.execute(_CONSUME_SQL, {"id": row_id, "marker": f"{MARKER_PREFIX}%"})
        row = cur.fetchone()
        if row is None:
            reason = _diagnose(cur, row_id)
            conn.rollback()
            return False, reason
        conn.commit()
        return True, f"arm-row {row_id} verified + consumed (created {row[1]})"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arm-row", required=True, type=int, dest="row_id")
    ap.add_argument("--target", required=True, help="the reset_auditor.sh SHORT target name, e.g. 'quality'")
    ap.add_argument("--mode", required=True, choices=("check", "consume"))
    args = ap.parse_args(argv)

    try:
        dsn = dsn_from_env_file()
    except Exception as e:  # noqa: BLE001 -- fail LOUD, caller refuses
        print(f"reset_auditor_arm_verify: no DSN: {e!r}", file=sys.stderr)
        return 2

    fn = check if args.mode == "check" else consume
    try:
        with psycopg.connect(dsn, connect_timeout=10) as conn:
            ok, reason = fn(conn, args.row_id, args.target)
    except Exception as e:  # noqa: BLE001 -- fail LOUD, caller refuses
        print(f"reset_auditor_arm_verify: DB error: {e!r}", file=sys.stderr)
        return 2

    print(reason, file=(sys.stdout if ok else sys.stderr))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
