"""Apply migration 035 — operator_backlog (the console's realtime "Your asks").

CLAUDE.md forbids `supabase db push` against prod (decision-962). Use this direct
psycopg-apply instead. It (1) creates the table + RLS/grants (idempotent), then
(2) SEEDS it — ONLY when empty — from the curated items in
reports/operator-asks-backlog.html, preserving the section order
(needs_you -> in_progress -> done).

Usage:
  python scripts/apply_operator_backlog.py            # dry-run (rolled back)
  python scripts/apply_operator_backlog.py --apply    # commit
"""
from __future__ import annotations

import os
import pathlib
import sys

import psycopg
from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "migrations" / "035_operator_backlog.sql"

# Seed rows parsed from reports/operator-asks-backlog.html (session snapshot,
# 2 Aug 2026). 🔴 -> needs_you, 🟡 -> in_progress, 🟢 -> done. Order preserved
# via sort_order; status-priority ordering is applied at read time.
SEED = [
    # (status, ask, note, op_ref)
    ("needs_you", "Head-of-revenue: two decisions",
     "(1) dedicated finance-agent vs a console function? (2) where do I pull real billing — which card/dashboard for the Claude Max subs (the biggest line)?",
     "op#8864"),
    ("needs_you", "HeartMuLa music on your Abu Dhabi GPU desktop",
     "You'll add the desktop to the tailnet + SSH key when back tonight → I stand up HeartMuLa (real-orchestra open model on real CUDA).",
     "op#9046"),
    ("needs_you", "SRE auto-recycle of lanes — approach",
     "Answered: capability built but doctrine holds SRE to page-only. Your call: hub-identity red-reset vs a cai carve-out. Rec: arm post-demo.",
     "op#8995"),
    ("needs_you", "Token remediation (Studio burned Owner token + Mini on Gazzabyte's token)",
     "Exposure not outage. You have the replace commands; I verify the auth fp after.",
     None),
    ("in_progress", "Video pipeline — the big one",
     "Editor built (comment/trim/dedup/full-library, mobile-fixed). Now reworking the DEFAULT cut for real variety + following the 3 prior batches' storyboards, mined into a reusable template + pipeline runbook, designed so gap-analysis (what to shoot) drops in once ingestion lands.",
     "op#9024·9040·9050·9074·9076·9078"),
    ("in_progress", "Sovereign music — Stable Audio Open on the Studio",
     "Studio reachable + MPS confirmed; env setup underway. HeartMuLa (your desktop) is the better path once access lands tonight.",
     "op#9046"),
    ("in_progress", "Clear the irsyad backlog — full tilt",
     "Displacement-guard: DONE + live. Tin-edit relax (mig134): re-proved zero-holes, now at CI → cai grant → apply. Platform toggles (module + school-split): 2nd lane (cc-ihsanos-1) speccing. NETS + sales@ enrollment: authorized, hub tasking.",
     "op#8999·9029·9042"),
    ("in_progress", "Payment-agnostic rails, built for irsyad (their store) reusably",
     "School fees + storefront checkout, payment-agnostic. Building for irsyad's own store first, as reusable substrate. (Noted as a standing principle for everything we build for irsyad.)",
     "op#16:50–16:57"),
    ("in_progress", "This backlog",
     "You're looking at it. I'll keep it current as things clear.",
     "op#9081"),
    ("done", "Arm the context-health watchdog alerting",
     "amber pages + auto-checkpoint; test page proven. Root cause was a live-vs-repo plist drift.",
     "op#8985"),
    ("done", "Fix the reset-button double-/clear (all 3 + console)",
     "Root-caused (callbacks skipped the dedupe gate), fixed + deployed on both hosts, regression test.",
     "op#8989·8992"),
    ("done", "Show token attribution on the console + shift all lanes to your token",
     "🔑 badges live; all worker lanes migrated to your token + Opus 4.8 (per your \"4.8 not 5\").",
     "op#9015·9017·9020"),
    ("done", "More lanes / full-tilt parallelism",
     "2nd platform lane (cc-ihsanos-1) stood up with its own inbox (caught + avoided a shared-inbox collision).",
     "op#9029·8973"),
    ("done", "Mobile-friendly video editor",
     "Fixed + released the phone-ready link (caught + corrected a false negative in my own check).",
     "op#9050"),
    ("done", "Publish platform docs + refresh the client status board",
     "Both live + verified. A not-yet-live claim was caught + corrected before any client saw it.",
     None),
    ("done", "Music: are the beds AI? real orchestra? open-source? costs?",
     "Lyria = synthetic (rejected); real orchestra = Artlist (~$199/yr) or Suno; sovereign = Stable Audio Open / HeartMuLa. Routes chosen.",
     "op#9040·9046"),
]


def main() -> int:
    load_dotenv(ROOT / ".env")
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        print("DATABASE_URL not set", file=sys.stderr)
        return 1
    apply = "--apply" in sys.argv
    sql = MIGRATION.read_text()

    with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.operator_backlog')")
        print("operator_backlog BEFORE:", cur.fetchone()[0] or "absent")

        cur.execute(sql)  # BEGIN/COMMIT inside the file run within this tx

        cur.execute("SELECT to_regclass('public.operator_backlog')")
        if cur.fetchone()[0] is None:
            print("FAILED — table still absent", file=sys.stderr)
            conn.rollback()
            return 2

        cur.execute("SELECT count(*) FROM public.operator_backlog")
        existing = cur.fetchone()[0]
        print("existing rows:", existing)
        if existing == 0:
            for i, (status, ask, note, op_ref) in enumerate(SEED, start=1):
                cur.execute(
                    "INSERT INTO public.operator_backlog (ask, status, op_ref, note, sort_order) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (ask, status, op_ref, note, i),
                )
            print("seeded rows:", len(SEED))
        else:
            print("table already populated — skipping seed")

        cur.execute(
            "SELECT status, count(*) FROM public.operator_backlog GROUP BY status ORDER BY status")
        print("by status:", cur.fetchall())

        # Prove the read-only console role can SELECT (grant + RLS policy landed).
        cur.execute(
            "SELECT has_table_privilege('console_readonly','public.operator_backlog','SELECT')")
        print("console_readonly can SELECT:", cur.fetchone()[0])

        if apply:
            conn.commit()
            print("APPLIED + committed.")
        else:
            conn.rollback()
            print("DRY RUN — rolled back. Re-run with --apply to commit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
