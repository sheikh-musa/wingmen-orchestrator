"""reset_audit_row — write the pre-clear audit row for reset_auditor.sh (CAI-1392 C).

Exit 0 ONLY after the row commits; any failure exits non-zero so the caller aborts with the
body untouched (dead-man's switch: no audit row, no /clear). The row is self-addressed
(from == to == the caller's identity), the same shape sre_lane_recycle.audit_before_clear
uses, so it is attributable without paging anyone.

DSN is FILE-FIRST (substrate_dsn.dsn_from_env_file): a long-lived caller's inherited
DATABASE_URL may hold a pre-rotation password, and a stale-password connect extends the
pooler circuit-breaker lockout (op#24342).
"""
from __future__ import annotations

import argparse
import sys

from substrate_dsn import dsn_from_env_file


def _connect(dsn):
    import psycopg
    return psycopg.connect(dsn)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="pre-clear audit row for reset_auditor.sh")
    ap.add_argument("--by", required=True, help="caller identity (registered agent id)")
    ap.add_argument("--base", required=True)
    ap.add_argument("--session", required=True)
    ap.add_argument("--handoff", required=True)
    ap.add_argument("--reason", required=True)
    # RESET_FORCE bypasses the busy/queued gates and can discard an in-flight audit, so the
    # row must say it was used, which gate(s) it overrode, and why (orch-console #49369).
    ap.add_argument("--forced-gates", default="none", help="csv of gates FORCE overrode, or none")
    ap.add_argument("--busy-reason", default="")
    a = ap.parse_args(argv)
    forced = a.forced_gates not in ("", "none")
    subject = (f"{'FORCED ' if forced else ''}AUDITOR RESET (CAI-1392 C): in-place recycle of "
               f"{a.base} (session {a.session})")
    force_txt = (f"force=true — RESET_FORCE overrode: {a.forced_gates}"
                 + (f" (busy reason: {a.busy_reason}); in-flight work DISCARDED" if a.busy_reason else "")
                 if forced else "force=false — every gate passed on its own")
    body = (f"{a.by} is recycling auditor singleton {a.base} IN-PLACE via reset_auditor.sh "
            f"(same pid: token + model preserved). Gates: allowlist, has-session, self-fire, "
            f"busy, fresh handoff, queued-composer, armed. {force_txt}. Handoff: {a.handoff}. "
            f"Reason: {a.reason}")
    try:
        dsn = dsn_from_env_file()
        with _connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, body, "
                "priority, requires_response) VALUES (%s, %s, 'update', %s, %s, 'P2', false)",
                (a.by, a.by, subject, body))
            conn.commit()
    except Exception as e:  # noqa: BLE001 — fail LOUD, caller aborts
        print(f"reset_audit_row: FAILED to write audit row: {e!r}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
