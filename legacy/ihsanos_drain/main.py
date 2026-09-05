"""CADENCE-008 A single-cycle drain orchestrator. Report-only until both gates clear:
 (1) CADENCE-008 challenge window closed, (2) cai #2066 grant predicate ratified.
Launched per-fire by dev.wingmen.ihsanos-drain (StartInterval=1800)."""
from __future__ import annotations

import os

from legacy.ihsanos_drain.granted_work import candidate_query, summarize
from legacy.ihsanos_drain.kill_switch import drain_disabled
from legacy.ihsanos_drain.poller import inbox_query
from legacy.ihsanos_drain.report import build_report_row


def _execute_enabled() -> bool:
    return os.environ.get("DRAIN_EXECUTE_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


_INSERT_MESSAGE = (
    "INSERT INTO agent_messages "
    "(from_agent, to_agent, message_type, subject, body, requires_response, "
    " is_test, from_agent_verified, sub_tag, priority) "
    "VALUES (%(from_agent)s, %(to_agent)s, %(message_type)s, %(subject)s, "
    " %(body)s, %(requires_response)s, %(is_test)s, %(from_agent_verified)s, "
    " %(sub_tag)s, %(priority)s)"
)


def run_cycle(cur, *, caller_name: str, token_cap, execute_fn=None) -> dict:
    if drain_disabled():
        return {"mode": "disabled", "executed": 0, "polled": 0,
                "executable": [], "held": []}

    sql, params = inbox_query(limit=50)
    cur.execute(sql, params)
    polled = len(cur.fetchall())

    csql, cparams = candidate_query()
    cur.execute(csql, cparams)
    candidate_rows = cur.fetchall()
    grouped = summarize(candidate_rows)
    executable = grouped["executable"]
    held = grouped["held"]

    # EXECUTE ARM (CAI-RESP-212 / Option B2) — only when DRAIN_EXECUTE_ENABLED.
    # Each executable ruling runs in an isolated worktree behind the migration
    # gate + local pre-push filters, then opens a PR (never merges). Default
    # build is report-only: the flag stays false until window-close + validation
    # cycle #2 + a supervised first run.
    enabled = _execute_enabled()
    outcomes = []
    if enabled and executable:
        executor = execute_fn or make_executor(cur, caller_name=caller_name)
        exec_set = set(executable)
        for row in candidate_rows:
            if (row.get("decision_ref") if hasattr(row, "get") else None) in exec_set:
                outcomes.append(executor(dict(row)))

    if enabled:
        mode = "execute"
        statuses = [o.status for o in outcomes]
        body = (
            f"polled {polled}; would-execute {len(executable)}: {executable}; "
            f"executed {len(outcomes)} -> {statuses}; held {len(held)}: "
            f"{[ref for ref, _ in held]}"
        )
        report_only = False
    else:
        mode = "report_only"
        body = (
            f"polled {polled}; granted candidates {len(executable) + len(held)}; "
            f"would-execute {len(executable)}: {executable}; "
            f"held {len(held)}: {held}; "
            f"execute_enabled=False (report-only build)"
        )
        report_only = True

    report = build_report_row(summary=body, report_only=report_only)
    cur.execute(_INSERT_MESSAGE, report)
    return {
        "mode": mode,
        "executed": len(outcomes),
        "polled": polled,
        "executable": executable,
        "held": [ref for ref, _ in held],
        "outcomes": [(o.ruling_ref, o.status) for o in outcomes],
    }


def make_executor(cur, *, caller_name: str):
    """Build the real per-ruling executor (worktree -> claude -> migration gate
    -> local pre-push CI -> open PR), recording spend + posting an outcome report.
    IO boundary — the decision logic it drives is unit-tested in test_runner."""
    from legacy.ihsanos_drain import runner
    from legacy.ihsanos_drain.token_budget import record_spend_sql

    def _execute(ruling: dict):
        ref = ruling.get("decision_ref", "(unknown)")
        wt_path, branch = runner.create_worktree(ref)
        try:
            outcome = runner.execute_ruling(
                ruling,
                run_claude=lambda prompt: runner.run_claude_in_worktree(prompt, wt_path),
                changed_files_fn=lambda: runner.git_changed_files(wt_path),
                run_ci=lambda: runner.run_local_ci(wt_path),
                publish_fn=lambda: runner.publish_drain_pr(ref, ruling),
            )
        finally:
            runner.remove_worktree(wt_path, branch)

        try:  # ledger exists only post-go-live; never let bookkeeping mask outcome
            rsql, rparams = record_spend_sql(caller_name, outcome.tokens_spent, outcome.status)
            cur.execute(rsql, rparams)
        except Exception:
            pass

        report = build_report_row(
            summary=f"[{ref}] {outcome.status}: {outcome.detail}", report_only=False
        )
        cur.execute(_INSERT_MESSAGE, report)
        return outcome

    return _execute


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
