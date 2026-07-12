#!/usr/bin/env python3
"""apply_021_invariant_registry.py — direct-psycopg apply (CAI-RESP-420, task #51).

Creates invariant_registry (DDL from migrations/021_invariant_registry.sql) and
PARAMETERIZED-seeds cai's ratified v1 invariant set (seeded_by='cc-infra-seeded',
for cai to review/adjust then steward). Idempotent: DDL guarded, seed is
ON CONFLICT DO NOTHING (never clobbers a row cai has since edited).

    .venv/bin/python3 scripts/apply_021_invariant_registry.py [--dry-run]

NEVER db push (CLAUDE.md / decision 962). Applies to the substrate (DATABASE_URL).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

ORCH = Path(__file__).resolve().parent.parent
load_dotenv(ORCH / ".env")
DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
SQL_FILE = ORCH / "migrations" / "021_invariant_registry.sql"

# cai's ratified v1 set (CAI-RESP-420). gate_status honest to current reality:
# 'pending' where the gate is being built now (MIGRATION-1/SCHEMA-1), else 'MANUAL'
# (human-tracked / another lane's gate not yet wired). cai adjudicates from here.
# (ref, domain, statement, gate_ref, gate_status, severity, origin_incident)
SEED = [
    ("MONEY-1", "money", "No anon/authenticated table-write on money tables.",
     "manual money-table grant audit", "MANUAL", "critical", "money-gate doctrine"),
    ("MONEY-2", "money", "Two-signature (preparer <> endorser) required to close.",
     "manual two-sig review", "MANUAL", "critical", "money-gate doctrine"),
    ("MONEY-3", "money", "service_role-only order/money INSERT (no direct client insert).",
     "manual + drift-detector RLS/grant check", "MANUAL", "critical", "092/storefront anon-write"),
    ("MONEY-4", "money",
     "Identity-bearing SECDEF money fns must NOT trust a caller-supplied actor/endorser id — "
     "pin to auth.uid() OR a documented+gated service_role-only trusted-caller boundary.",
     "SECDEF money-fn audit (tabung_endorse_close_report, tabung_mark_banked_atomic, siblings)",
     "MANUAL", "critical", "#7627"),
    ("MONEY-5", "money",
     "Money-path proof artifacts (deposit slips) are write-once AND undeletable by users "
     "(storage RLS SELECT+INSERT only, no authenticated DELETE).",
     "storage-policy audit on money buckets", "MANUAL", "critical", "CAI-RESP-417/091"),
    ("MONEY-6", "money",
     "A money-path capability is 'done' only when the REAL user flow is driven end-to-end; "
     "money-path UI gates are in scope for the money-gate review.",
     "pre-mortem/verify drives the real flow (not DB/RPC only)", "MANUAL", "high", "CAI-RESP-415"),
    ("RESIDENCY-1", "residency",
     "No client rows in another silo; the write-target silo is verified pre-live.",
     "manual residency gate (pre-live)", "MANUAL", "critical", "TENANT-RESIDENCY-001"),
    ("MIGRATION-1", "schema",
     "An applied migration is immutable AND byte-identical across every silo it is tracked on "
     "(bans the in-place amend that caused 092; covers 073/074 out-of-band + tin-RLS-088 divergences).",
     "cross-silo drift-detector + migration-immutability guard (task #50)", "pending", "critical",
     "092/goumlyne (061 in-place amend)"),
    ("SCHEMA-1", "schema",
     "Every column/table the app code touches EXISTS in every silo (schema >= code-contract).",
     "cross-silo drift-detector (task #49/#50)", "pending", "critical", "092/goumlyne"),
    ("DEPLOY-1", "deploy",
     "The live surface serves the COMMITTED code (fc-vN drift lineage).",
     "code-vs-deployed reconciler", "MANUAL", "high", "fc-vN deploy drift"),
    ("AUTHORITY-1", "authority",
     "Live-money grants + window waivers act ONLY on VERIFIED inbound operator_messages, "
     "never agent-relayed authority.",
     "grant-flip checklist requires a cited verified operator_messages id", "MANUAL", "critical",
     "expedite arc"),
    ("TOKENS-1", "tokens",
     "Money/security adjudication is never silently downgraded or torn down under token pressure; "
     "cap pressure QUEUES gated money work.",
     "autoscaler decision-log audit + model-floor config (CAI-RESP-419)", "MANUAL", "high",
     "MODEL-POLICY-001/CAI-RESP-419"),
    ("LAYER-VOCAB-001", "governance",
     "No bare product-name data references; name the layer + exact store + project ref.",
     "existing layer-vocab lint", "MANUAL", "medium", "LAYER-VOCAB-001"),
]

TRACKER = ("20260712000000", "021_invariant_registry")


def _statements(sql: str):
    # Strip line comments FIRST (comments contain semicolons, e.g. "gate run;
    # staleness"; a naive split would fracture CREATE TABLE mid-statement). Safe
    # here: this DDL has no '--' inside any string literal. No dollar-quoted bodies.
    stripped = "\n".join(
        (line[:line.index("--")] if "--" in line else line)
        for line in sql.splitlines()
    )
    return [s.strip() for s in stripped.split(";") if s.strip()]


def main() -> int:
    dry = "--dry-run" in sys.argv
    ddl = SQL_FILE.read_text()
    print(f"apply 021_invariant_registry -> substrate (dry_run={dry})")
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id','cc-infra',true)")
        for stmt in _statements(ddl):
            head = " ".join(stmt.split())[:70]
            print(f"  DDL: {head}")
            if not dry:
                cur.execute(stmt)
        for ref, dom, stmt_txt, gref, gstat, sev, orig in SEED:
            print(f"  SEED: {ref} [{gstat}]")
            if not dry:
                cur.execute(
                    "INSERT INTO invariant_registry "
                    "(invariant_ref, domain, statement, gate_ref, gate_status, severity, "
                    " origin_incident, seeded_by) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,'cc-infra-seeded') "
                    "ON CONFLICT (invariant_ref) DO NOTHING",
                    (ref, dom, stmt_txt, gref, gstat, sev, orig),
                )
        if not dry:
            cur.execute(
                "INSERT INTO supabase_migrations.schema_migrations (version, name, statements) "
                "VALUES (%s,%s,%s) ON CONFLICT (version) DO NOTHING",
                (TRACKER[0], TRACKER[1],
                 ["CREATE TABLE invariant_registry (CAI-RESP-420 substrate invariant enumeration)",
                  "RLS deny-all + REVOKE (service-role-only)",
                  f"seed {len(SEED)} v1 invariants (cc-infra-seeded, cai stewards)"]),
            )
            conn.commit()
        else:
            conn.rollback()
    if not dry:
        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*), count(*) FILTER (WHERE gate_status='pending') "
                        "FROM invariant_registry")
            total, pending = cur.fetchone()
            print(f"applied: {total} invariants seeded ({pending} pending gates in build)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
