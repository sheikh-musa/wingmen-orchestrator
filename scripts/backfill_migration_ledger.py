"""backfill_migration_ledger.py — VERIFIED backfill of migration_ledger 044..056 (CAI-RESP-1054).

WHY: migration_ledger went stale after 043 — every migration from 044 to 054 was applied to
the orchestrator substrate with no ledger row. cai ruled it a DEAD MEASURER: a ledger that
still looks authoritative while recording nothing is worse than no ledger, because it reads
as an answer to "is what is applied what is in the repo" — the question the never-use-db-push
rule exists to make answerable.

WHY VERIFIED RATHER THAN BLIND: writing sha256(file-as-of-today) and calling it "applied"
would assert a verification nobody performed — the same defect in a different place. So each
row is backfilled ONLY after checking, at source, that the objects the migration claims to
create actually exist (or, for grant/column-only migrations, that their EFFECT is present).
The `note` column records exactly what was checked, so a reader can tell a verified row from
an assumed one WITHOUT re-running this.

WHAT IT STILL CANNOT TELL YOU, stated rather than glossed: the sha256 is of the file as it
stands today. If a migration file was edited after being applied, this proves the objects
exist — NOT that the applied DDL was byte-identical to the current file. That gap closes
going forward via the non-skippable write (CAI-RESP-1054 item 2), not retroactively.

Usage:
  python scripts/backfill_migration_ledger.py            # dry-run (report only)
  python scripts/backfill_migration_ledger.py --apply    # insert missing rows
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import sys

import psycopg
from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parent.parent
SILO = "tscuymavysscrvoberrr"

# Objects each migration claims to create. Grant/column-only migrations declare an
# EFFECT probe instead — a migration that creates nothing still has to be checkable.
EXPECT: dict[str, dict] = {
    "044_operator_asks.sql":                        {"table": ["operator_asks"]},
    "045_share_lane_status.sql":                    {"table": ["share_lane_labels", "share_pool_map"],
                                                     "view": ["share_lane_status_v", "share_pool_status_v"]},
    "046_fleet_proposals.sql":                      {"table": ["fleet_proposals"], "view": ["fleet_proposal_metrics_v"]},
    "047_invariant_registry_honesty.sql":           {"view": ["invariant_registry_state"]},
    "048_lane_task_acceptance.sql":                 {"view": ["lane_tasks_state"]},
    "049_invariant_assertion_runs.sql":             {"table": ["invariant_assertion_runs"]},
    "050_invariant_registry_state_cai1028.sql":     {"view": ["invariant_registry_state"]},
    "051_held_commitments.sql":                     {"table": ["held_commitments"], "view": ["held_commitments_due"]},
    "052_escalator_anon_execute_revoke.sql":        {"effect": "no_anon_execute:escalate_full_tier_without_auditor"},
    "053_pool_usage_5h_reset.sql":                  {"effect": "column:pool_usage.resets_5h_at"},
    "054_sla_observed_recipient_activity.sql":      {"view": ["inbox_sla_violations", "agent_observed_activity"]},
    "055_close_anon_read_on_five_rls_off_tables.sql": {"policy": [("chat_members", "chat_members_console_readonly_select")],
                                                       "effect": "rls_on:held_commitments"},
    "056_anon_reachable_views_security_invoker.sql": {"effect": "security_invoker:held_commitments_due"},
}


def check(cur, kind: str, spec) -> tuple[bool, str]:
    if kind == "table":
        cur.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname='public' AND c.relkind='r' AND c.relname=ANY(%s)", (spec,))
        n = cur.fetchone()[0]
        return n == len(spec), f"tables {spec}: {n}/{len(spec)} present"
    if kind == "view":
        cur.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname='public' AND c.relkind='v' AND c.relname=ANY(%s)", (spec,))
        n = cur.fetchone()[0]
        return n == len(spec), f"views {spec}: {n}/{len(spec)} present"
    if kind == "policy":
        okall, bits = True, []
        for tbl, pol in spec:
            cur.execute("SELECT count(*) FROM pg_policies WHERE schemaname='public' AND tablename=%s AND policyname=%s",
                        (tbl, pol))
            hit = cur.fetchone()[0] == 1
            okall = okall and hit
            bits.append(f"{tbl}.{pol}={'yes' if hit else 'NO'}")
        return okall, "policies " + ", ".join(bits)
    if kind == "effect":
        mode, arg = spec.split(":", 1)
        if mode == "no_anon_execute":
            cur.execute("SELECT p.oid FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                        "WHERE n.nspname='public' AND p.proname=%s LIMIT 1", (arg,))
            row = cur.fetchone()
            if not row:
                return False, f"effect no_anon_execute:{arg}: function ABSENT"
            cur.execute("SELECT has_function_privilege('anon', %s, 'EXECUTE')", (row[0],))
            return (not cur.fetchone()[0]), f"effect anon EXECUTE on {arg} revoked"
        if mode == "column":
            tbl, col = arg.split(".")
            cur.execute("SELECT count(*) FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name=%s AND column_name=%s", (tbl, col))
            return cur.fetchone()[0] == 1, f"effect column {arg} present"
        if mode == "rls_on":
            cur.execute("SELECT relrowsecurity FROM pg_class WHERE oid=('public.'||%s)::regclass", (arg,))
            return bool(cur.fetchone()[0]), f"effect RLS enabled on {arg}"
        if mode == "security_invoker":
            cur.execute("SELECT c.reloptions FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                        "WHERE n.nspname='public' AND c.relname=%s", (arg,))
            r = cur.fetchone()
            return bool(r) and "security_invoker=on" in str(r[0]), f"effect security_invoker on {arg}"
    return False, f"unknown check {kind}"


def main() -> int:
    load_dotenv(ROOT / ".env")
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        print("no DATABASE_URL", file=sys.stderr)
        return 1
    apply = "--apply" in sys.argv

    with psycopg.connect(dsn, connect_timeout=25) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id','orch-console',true)")
        cur.execute("ALTER TABLE migration_ledger ADD COLUMN IF NOT EXISTS note text")
        cur.execute("SELECT migration_name FROM migration_ledger WHERE silo_ref=%s", (SILO,))
        have = {r[0] for r in cur.fetchall()}

        inserted = skipped = failed = 0
        for name in sorted(EXPECT):
            path = ROOT / "migrations" / name
            if not path.exists():
                print(f"  MISSING FILE {name}")
                failed += 1
                continue
            if name in have:
                print(f"  have      {name}")
                skipped += 1
                continue
            results, allok = [], True
            for kind, spec in EXPECT[name].items():
                good, detail = check(cur, kind, spec)
                allok = allok and good
                results.append(("ok" if good else "FAIL") + " " + detail)
            note = ("BACKFILL 2026-08-17 (CAI-RESP-1054). Verified at source: " + "; ".join(results)
                    + ". sha256 is of the file AS OF BACKFILL, not proof the applied DDL was byte-identical.")
            if not allok:
                print(f"  UNVERIFIED {name} -> {results}  (NOT inserted)")
                failed += 1
                continue
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            if apply:
                cur.execute("INSERT INTO migration_ledger (repo, migration_name, silo_ref, sha256, applied_by, note) "
                            "VALUES ('orchestrator', %s, %s, %s, 'orch-console (backfill)', %s)",
                            (name, SILO, sha, note))
            print(f"  {'INSERT   ' if apply else 'would-add'} {name} -> {'; '.join(results)}")
            inserted += 1

        if apply:
            conn.commit()
        print(f"\n{'inserted' if apply else 'would insert'}={inserted}  already-present={skipped}  unverified/failed={failed}")
        if failed:
            print("UNVERIFIED rows were NOT written — an unverified ledger row is the defect this fixes.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
