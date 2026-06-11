"""CADENCE-008 A single-cycle drain orchestrator. Report-only until both gates clear:
 (1) CADENCE-008 challenge window closed, (2) cai #2066 grant predicate ratified.
Launched per-fire by dev.wingmen.ihsanos-drain (StartInterval=1800)."""
from __future__ import annotations

import os

from ihsanos_drain.granted_work import candidate_query, summarize
from ihsanos_drain.kill_switch import drain_disabled
from ihsanos_drain.poller import inbox_query
from ihsanos_drain.report import build_report_row


def _execute_enabled() -> bool:
    return os.environ.get("DRAIN_EXECUTE_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def run_cycle(cur, *, caller_name: str, token_cap) -> dict:
    if drain_disabled():
        return {"mode": "disabled", "executed": 0, "polled": 0,
                "executable": [], "held": []}

    sql, params = inbox_query(limit=50)
    cur.execute(sql, params)
    polled = len(cur.fetchall())

    csql, cparams = candidate_query()
    cur.execute(csql, cparams)
    grouped = summarize(cur.fetchall())
    executable = grouped["executable"]
    held = grouped["held"]

    # REPORT-ONLY: even when `executable` is non-empty the execute arm (plan
    # Task 7) is intentionally absent until both gates clear — never spawns
    # claude -p, never mutates source. It only reports what it WOULD execute.
    body = (
        f"polled {polled}; granted candidates {len(executable) + len(held)}; "
        f"would-execute {len(executable)}: {executable}; "
        f"held {len(held)}: {held}; "
        f"execute_enabled={_execute_enabled()} (report-only build)"
    )
    report = build_report_row(summary=body, report_only=True)
    cur.execute(
        "INSERT INTO agent_messages "
        "(from_agent, to_agent, message_type, subject, body, requires_response, "
        " is_test, from_agent_verified, sub_tag, priority) "
        "VALUES (%(from_agent)s, %(to_agent)s, %(message_type)s, %(subject)s, "
        " %(body)s, %(requires_response)s, %(is_test)s, %(from_agent_verified)s, "
        " %(sub_tag)s, %(priority)s)",
        report,
    )
    return {
        "mode": "report_only",
        "executed": 0,
        "polled": polled,
        "executable": executable,
        "held": [ref for ref, _ in held],
    }


def _main() -> int:
    import psycopg
    from dotenv import load_dotenv
    from psycopg.rows import dict_row

    load_dotenv("/Users/sheikhmusa/wingmen/orchestrator/.env")
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn, \
            conn.cursor() as cur:
        result = run_cycle(cur, caller_name="ihsanos-drain", token_cap=400_000)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
