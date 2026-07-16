"""Guarded apply for the Execution-Reliability Layer (op#4711 / CAI-RESP-464).

AUTHORED for the HUB to run. CLAUDE.md forbids `supabase db push` to prod
(decision-962); this is the direct psycopg-apply path. It refuses to touch any DB
whose connection ref does not match --expect-ref, and is dry-run (rolled back) by
default.

Usage:
  # dry-run against the expected substrate (rolls back):
  python scripts/apply_exec_reliability_layer.py --expect-ref tscuymavysscrvoberrr
  # actually apply + commit:
  python scripts/apply_exec_reliability_layer.py --expect-ref tscuymavysscrvoberrr --apply

The DDL body is rendered from the single source of truth
(nervous_system.exec_reliability.schema) so this can never drift from the
authored migration file or the module/test code.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from nervous_system.exec_reliability import schema  # noqa: E402

SUBSTRATE_REF = "tscuymavysscrvoberrr"


def _ref_in_dsn(dsn: str, expect_ref: str) -> bool:
    """The Supabase pooler DSN embeds the project ref (postgres.<ref> user / host).

    We refuse to run unless the expected ref is literally present in the DSN — a
    fail-closed guard against pointing this at the wrong database.
    """
    return expect_ref in dsn


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--expect-ref", required=True,
                    help="project ref that MUST be present in the DSN (fail-closed)")
    ap.add_argument("--apply", action="store_true",
                    help="commit (default: dry-run / rollback)")
    args = ap.parse_args()

    load_dotenv(os.path.expanduser("~/wingmen/orchestrator/.env"))
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL / SUPABASE_DB_URL not set")

    if not _ref_in_dsn(dsn, args.expect_ref):
        raise SystemExit(
            f"REFUSING: --expect-ref {args.expect_ref!r} not present in the DSN. "
            "Wrong database? Aborting before any write."
        )

    # We manage the transaction ourselves for the dry-run/rollback path, so run
    # the DDL body (no begin/commit) under our own connection transaction.
    body = schema.full_migration_sql("public", "exec_runner")

    # The runner posts attributable bus rows; agent_messages.from_agent is FK ->
    # agents(id), so the runner agent must be registered. Idempotent + part of
    # wiring the layer. Runner agent id is configurable at runtime; the default
    # 'cc-exec-runner' is registered here.
    runner_agent = "cc-exec-runner"
    register_runner = f"""
    insert into public.agents (id, display_name, status)
    values ('{runner_agent}', 'Exec-Reliability Runner', 'offline')
    on conflict (id) do nothing;
    """

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(body)
            cur.execute(register_runner)
            # Post-apply verification.
            cur.execute("select to_regclass('public.exec_work_items')")
            wi = cur.fetchone()[0]
            cur.execute("select to_regclass('public.exec_delivery_ledger')")
            dl = cur.fetchone()[0]
            cur.execute("select 1 from pg_roles where rolname = 'exec_runner'")
            role_ok = cur.fetchone() is not None
            # EXEC-4 assertions: runner has NO write on strategic_decisions.
            cur.execute(
                "select has_table_privilege('exec_runner','public.strategic_decisions','UPDATE'), "
                "has_table_privilege('exec_runner','public.strategic_decisions','INSERT'), "
                "has_table_privilege('exec_runner','public.exec_work_items','UPDATE'), "
                "has_table_privilege('exec_runner','public.exec_work_items','INSERT')"
            )
            sd_upd, sd_ins, wi_upd, wi_ins = cur.fetchone()

            print(f"exec_work_items:        {wi}")
            print(f"exec_delivery_ledger:   {dl}")
            print(f"exec_runner role:       {role_ok}")
            print(f"runner UPDATE strategic_decisions (must be False): {sd_upd}")
            print(f"runner INSERT strategic_decisions (must be False): {sd_ins}")
            print(f"runner UPDATE exec_work_items      (must be True):  {wi_upd}")
            print(f"runner INSERT exec_work_items      (must be False): {wi_ins}")

            ok = (
                wi is not None and dl is not None and role_ok
                and sd_upd is False and sd_ins is False
                and wi_upd is True and wi_ins is False
            )
            if args.apply and ok:
                conn.commit()
                print("\nAPPLIED + committed.")
                return 0
            if args.apply:
                conn.rollback()
                print("\nABORTED: post-apply invariants not satisfied; not committing.")
                return 1
            conn.rollback()
            print("\nDRY-RUN (rolled back). Re-run with --apply to commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
