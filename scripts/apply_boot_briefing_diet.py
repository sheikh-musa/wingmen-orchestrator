"""SUBSTRATE-COHERENCE-001 C + F: boot_briefing diet (psycopg-apply).

Reconstructs boot_briefing from its LIVE definition via arm-level surgery
(split on UNION ALL, edit only the targeted arms, preserve all others
verbatim). This avoids the decision-962 hazard where a hand-retyped
CREATE OR REPLACE VIEW silently drops arms.

Changes:
  F  — drop the 'repo_snapshot' arm (stale since Apr 16; repo_context is live).
  C1 — collapse 'inbox_sla_violation' to one aggregate row per agent.
  C2 — diet 'active_decision': exclude is_test + limit to last-30-days OR a
       pinned constitutional set (PINNED below; cai-amendable).

CLAUDE.md forbids `supabase db push` to prod; use this direct psycopg-apply.

Usage:
  python scripts/apply_boot_briefing_diet.py            # dry-run (rolled back)
  python scripts/apply_boot_briefing_diet.py --apply    # commit
"""
from __future__ import annotations

import os
import re
import sys

import psycopg
from dotenv import load_dotenv

# Constitutional pin set — seeded small + clearly editable. cai may amend.
PINNED = [
    "CAI-SIMPLICITY-001",
    "CAI-PING-PROTOCOL-001",
    "CAI-PROCESS-INBOX-CADENCE-001",
    "CAI-HIERARCHY-001",
    "BUG-024",
]

ARM_RE = re.compile(r"SELECT '([a-z_]+)'::text AS source")

INBOX_SLA_AGG_ARM = """ SELECT 'inbox_sla_violation'::text AS source,
    isv.agent AS key,
    json_build_object('agent', isv.agent, 'count', count(*), 'oldest_created_at', min(isv.created_at), 'max_elapsed_min', max(isv.elapsed_minutes)) AS context
   FROM inbox_sla_violations isv
  GROUP BY isv.agent"""


def transform(viewdef: str) -> str:
    arms = re.split(r"\nUNION ALL\n", viewdef)
    out = []
    seen = {"repo_snapshot": 0, "inbox_sla_violation": 0, "active_decision": 0}
    pin_sql = ", ".join(f"'{r}'" for r in PINNED)
    for arm in arms:
        m = ARM_RE.search(arm)
        src = m.group(1) if m else None
        if src == "repo_snapshot":
            seen["repo_snapshot"] += 1
            continue  # F: drop
        if src == "inbox_sla_violation":
            seen["inbox_sla_violation"] += 1
            out.append(INBOX_SLA_AGG_ARM)  # C1: aggregate
            continue
        if src == "active_decision":
            seen["active_decision"] += 1
            new_arm, n = re.subn(
                r"  WHERE sd\.status = 'active'::text",
                "  WHERE sd.status = 'active'::text AND sd.is_test IS NOT TRUE "
                "AND (sd.decided_at >= (now() - '30 days'::interval) "
                f"OR sd.decision_ref = ANY (ARRAY[{pin_sql}]::text[]))",
                arm,
            )
            assert n == 1, f"active_decision WHERE anchor matched {n} times"
            out.append(new_arm)
            continue
        out.append(arm)
    assert seen == {"repo_snapshot": 1, "inbox_sla_violation": 1, "active_decision": 1}, \
        f"unexpected arm match counts: {seen}"
    return "\nUNION ALL\n".join(out)


def main() -> int:
    apply = "--apply" in sys.argv
    load_dotenv("/Users/sheikhmusa/wingmen/orchestrator/.env")
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL / SUPABASE_DB_URL not set")

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("select count(*) from boot_briefing")
            before = cur.fetchone()[0]
            cur.execute("select pg_get_viewdef('boot_briefing'::regclass, true)")
            viewdef = cur.fetchone()[0]

            new_def = transform(viewdef)
            cur.execute(f"CREATE OR REPLACE VIEW boot_briefing AS {new_def}")

            cur.execute("select count(*) from boot_briefing")
            after = cur.fetchone()[0]
            cur.execute("select source, count(*) from boot_briefing group by source order by 2 desc")
            by_source = cur.fetchall()

            print(f"boot_briefing rows: {before} -> {after}  (target <150)")
            print("by source after:")
            for s, n in by_source:
                print(f"  {s!r:30} {n}")

            if apply and after < 150:
                conn.commit()
                print("\nAPPLIED + committed.")
            elif apply:
                conn.rollback()
                print(f"\nABORTED: {after} rows still >= 150; not committing.")
                return 1
            else:
                conn.rollback()
                print("\nDRY-RUN (rolled back). Re-run with --apply to commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
