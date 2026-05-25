"""CAI-RESP-168 §4 tripwire reporter — invoked at +24h / +48h / +72h post
watchdog kickstart (2026-05-26 04:09 SGT). Sends a notification_log
distribution report to cai on the CAI-RESP-163 thread.

Usage:
    python scripts/watchdog_tripwire_report.py --window 24h
    python scripts/watchdog_tripwire_report.py --window 48h
    python scripts/watchdog_tripwire_report.py --window 72h

Idempotent: refuses to re-send the same window's report within 12h
(checks existing agent_messages on the thread).
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

import psycopg
from dotenv import load_dotenv

load_dotenv("/Users/sheikhmusa/wingmen/orchestrator/.env")

DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
THREAD_ID = "ae5153ec-94a7-4934-b317-82e95009aae4"
KICKSTART_UTC = "2026-05-25 20:09:54+00:00"  # 2026-05-26 04:09 SGT
SUBJECT_PREFIX = "Phase B watchdog tripwire"


def fetch_distribution(cur, since_iso: str) -> dict:
    cur.execute(
        """
        SELECT source, count(*) FROM notification_log
         WHERE source IN (
           'watchdog_hard_kill',
           'watchdog_pre_kill_audit',
           'watchdog_pre_kill_audit_failed',
           'watchdog_aborted_kill'
         )
           AND created_at >= %s
         GROUP BY source
        """,
        (since_iso,),
    )
    return {row[0]: row[1] for row in cur.fetchall()}


def fetch_hard_kills(cur, since_iso: str) -> list:
    cur.execute(
        """
        SELECT id, recipient, message_text, created_at
          FROM notification_log
         WHERE source = 'watchdog_hard_kill'
           AND created_at >= %s
         ORDER BY created_at
        """,
        (since_iso,),
    )
    return cur.fetchall()


def fetch_monitored(cur) -> list:
    cur.execute(
        """
        SELECT caller_name, sessions_24h, signal_a_match, signal_b_match,
               signal_c_match, first_observed_at, expires_at
          FROM watchdog_monitored_callers
         WHERE expires_at > now()
         ORDER BY first_observed_at DESC
        """
    )
    return cur.fetchall()


def already_sent(cur, window: str) -> bool:
    cur.execute(
        """
        SELECT count(*) FROM agent_messages
         WHERE thread_id = %s
           AND from_agent = 'cc-orchestrator'
           AND subject ILIKE %s
           AND is_test = false
           AND created_at >= now() - interval '12 hours'
        """,
        (THREAD_ID, f"%{SUBJECT_PREFIX} +{window}%"),
    )
    return cur.fetchone()[0] > 0


def is_probe_class(recipient: str, msg_text: str) -> bool:
    """Heuristic: probe-class callers have 'test', 'probe', or 'synthetic' in
    the recipient name. Used to flag NON-probe-class hard_kills for P1 escalation."""
    needle = (recipient or "").lower()
    return any(p in needle for p in ("test", "probe", "synthetic"))


def main(window: str) -> int:
    if not DSN:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        return 1

    with psycopg.connect(DSN, autocommit=True) as conn, conn.cursor() as cur:
        if already_sent(cur, window):
            print(f"tripwire +{window} report already sent in last 12h — skipping")
            return 0

        distribution = fetch_distribution(cur, KICKSTART_UTC)
        hard_kills = fetch_hard_kills(cur, KICKSTART_UTC)
        monitored = fetch_monitored(cur)

        non_probe_hard_kills = [hk for hk in hard_kills if not is_probe_class(hk[1], hk[2])]
        priority = "P1" if non_probe_hard_kills else "P3"
        msg_type = "review_request" if non_probe_hard_kills else "agreed"

        subject = f"{SUBJECT_PREFIX} +{window} — {len(non_probe_hard_kills)} non-probe hard_kill(s); {distribution.get('watchdog_hard_kill', 0)} total hard_kill, {len(monitored)} monitored active"

        body_lines = [
            f"Tripwire report +{window} per CAI-RESP-168 §4. Generated {datetime.now(timezone.utc).isoformat()}.",
            "",
            "NOTIFICATION_LOG DISTRIBUTION (since kickstart 2026-05-26 04:09 SGT):",
        ]
        for source in (
            "watchdog_hard_kill",
            "watchdog_pre_kill_audit",
            "watchdog_pre_kill_audit_failed",
            "watchdog_aborted_kill",
        ):
            body_lines.append(f"  {source}: {distribution.get(source, 0)}")
        body_lines.extend(["", f"MONITORED CALLERS (active, non-expired): {len(monitored)}"])
        for caller_name, sessions_24h, sa, sb, sc, observed, expires in monitored[:20]:
            matches = "".join(["1" if m is True else "0" if m is False else "?" for m in (sa, sb, sc)])
            body_lines.append(
                f"  {caller_name}: signals={matches} sessions_24h={sessions_24h} "
                f"first_seen={observed} expires={expires}"
            )

        body_lines.extend(["", f"HARD KILLS (total {len(hard_kills)}, non-probe {len(non_probe_hard_kills)}):"])
        for hk_id, recipient, msg_text, created_at in hard_kills:
            probe_marker = "[PROBE]" if is_probe_class(recipient, msg_text) else "[NON-PROBE]"
            body_lines.append(f"  msg #{hk_id} {probe_marker} caller={recipient} at={created_at}")

        if non_probe_hard_kills:
            body_lines.extend([
                "",
                "*** P1 ESCALATION ***",
                f"{len(non_probe_hard_kills)} non-probe-class hard_kill(s) detected in first 72h.",
                "Per CAI-RESP-168 §4: immediate cai review BEFORE next sweep.",
                "Operator should verify whether any kill was a false-positive against legitimate work.",
                "Panic button (WINGMEN_LONG_CALLER_WATCHDOG_DISABLED=true) is the immediate stop.",
            ])
        else:
            body_lines.extend([
                "",
                "ASSESSMENT: no non-probe-class hard_kill detected. Watchdog operating as designed for this window.",
            ])

        body = "\n".join(body_lines)

        cur.execute(
            """
            INSERT INTO agent_messages
              (thread_id, from_agent, to_agent, message_type, subject, body,
               requires_response, is_test, sub_tag, priority)
            VALUES (%s, 'cc-orchestrator', 'cai', %s, %s, %s,
                    %s, false, 'cc-orchestrator-2', %s)
            RETURNING id
            """,
            (
                THREAD_ID,
                msg_type,
                subject,
                body,
                bool(non_probe_hard_kills),
                priority,
            ),
        )
        msg_id = cur.fetchone()[0]
        print(f"tripwire +{window} report sent: msg #{msg_id} priority={priority}")
        return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--window", required=True, choices=["24h", "48h", "72h"])
    args = parser.parse_args()
    sys.exit(main(args.window))
