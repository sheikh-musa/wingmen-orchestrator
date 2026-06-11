"""SUBSTRATE-COHERENCE-001 E: identity canonicalization (psycopg-apply).

Normalizes the historical decided_by variants on strategic_decisions to the
canonical set and CHECK-constrains new rows. CLAUDE.md forbids
`supabase db push` against prod; use this direct psycopg-apply pattern.

Mapping is cai's, from the SUBSTRATE-COHERENCE-001 decision text:
  Musa, musa_cto                         -> musa
  cto, CAI, claude_ai, cai-musa-session  -> cai
  cc-ihsanos (Platform Agent)            -> cc-ihsanos
  claude_code -> per-row repo owner where determinable, else cc-orchestrator
     repos_affected == {ihsanos}    -> cc-ihsanos
     repos_affected == {cosem-tdu}  -> cc-cosem
     (single-repo wingmen-orchestrator, multi-repo, or null) -> cc-orchestrator

SCOPE NOTE — RESOLVED (cai #1990, option b). from_agent on agent_messages is
enforced by the existing validated FK to agents(id) (the closed canonical set).
The two non-canonical writers (ralph_runner, arch-030-escalation) are migrated:
they now post from_agent='substrate' with origin in sub_tag
('substrate-ralph-runner' / 'substrate-arch-030-escalation'). 'substrate' is a
registered agent. No separate CHECK is added — the FK already does the job.

Usage:
  python scripts/apply_identity_canon.py            # dry-run (no writes)
  python scripts/apply_identity_canon.py --apply    # commit the changes
"""
from __future__ import annotations

import os
import sys

import psycopg
from dotenv import load_dotenv

CANON = (
    "musa", "cai", "cc-ihsanos", "cc-orchestrator",
    "cc-scholar", "cc-cosem", "cc-wing", "substrate",
)

NORMALIZE_DECIDED_BY = """
update strategic_decisions
set decided_by = case
    when decided_by in ('Musa', 'musa_cto') then 'musa'
    when decided_by in ('cto', 'CAI', 'claude_ai', 'cai-musa-session') then 'cai'
    when decided_by = 'cc-ihsanos (Platform Agent)' then 'cc-ihsanos'
    when decided_by = 'claude_code' then case
        when cardinality(repos_affected) = 1 and repos_affected[1] = 'ihsanos'
            then 'cc-ihsanos'
        when cardinality(repos_affected) = 1 and repos_affected[1] = 'cosem-tdu'
            then 'cc-cosem'
        else 'cc-orchestrator'
    end
    else decided_by
end
where decided_by is not null
  and decided_by not in ('musa', 'cai', 'cc-ihsanos', 'cc-orchestrator',
                         'cc-scholar', 'cc-cosem', 'cc-wing', 'substrate');
"""

ADD_DECIDED_BY_CHECK = """
do $$
begin
    if not exists (
        select 1 from pg_constraint
        where conname = 'strategic_decisions_decided_by_canon'
    ) then
        alter table strategic_decisions
            add constraint strategic_decisions_decided_by_canon
            check (decided_by is null or decided_by in
                ('musa', 'cai', 'cc-ihsanos', 'cc-orchestrator',
                 'cc-scholar', 'cc-cosem', 'cc-wing', 'substrate'));
    end if;
end $$;
"""


def main() -> int:
    apply = "--apply" in sys.argv
    load_dotenv("/Users/sheikhmusa/wingmen/orchestrator/.env")
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL / SUPABASE_DB_URL not set")

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                select decided_by, count(*) from strategic_decisions
                where decided_by is not null and decided_by <> all(%s)
                group by decided_by order by 2 desc
            """, (list(CANON),))
            before = cur.fetchall()
            print("non-canonical decided_by BEFORE:")
            for v, n in before:
                print(f"  {v!r:30} {n}")

            cur.execute(NORMALIZE_DECIDED_BY)
            print(f"\nnormalized rows: {cur.rowcount}")

            cur.execute(ADD_DECIDED_BY_CHECK)

            cur.execute("""
                select decided_by, count(*) from strategic_decisions
                where decided_by is not null and decided_by <> all(%s)
                group by decided_by
            """, (list(CANON),))
            remaining = cur.fetchall()
            print(f"non-canonical decided_by AFTER: {remaining or 'none'}")

            if apply:
                conn.commit()
                print("\nAPPLIED + committed (decided_by normalized + CHECK added).")
            else:
                conn.rollback()
                print("\nDRY-RUN (rolled back). Re-run with --apply to commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
