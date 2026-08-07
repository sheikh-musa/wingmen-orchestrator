#!/usr/bin/env python3
"""Seed the `opportunities` ledger with the ~8 live revenue threads, at their
HONEST current state (Head of Revenue spec §3.1). Idempotent: ON CONFLICT
(slug) DO NOTHING — re-running never duplicates and never overwrites hand-edits
(the ledger is meant to be curated after seeding). Direct psycopg, decision-962.

Grounded in the operator's memory files (2026-07-22):
  shipforge_revenue / _fluid_clone_first_model, storefront_platform_vision,
  gazzabyte_irsyad_roadmap / _elly_client_relationship, sushitei_fb_vertical,
  alderei_invoice_tracker_pwa, ai_course_administrator_ray,
  shariah_compliant_uae_sea_rails, hafiz_fintech_partner, desmond_shen_partner,
  substrate_north_star_revenue_autonomy, revenue_first_payment_before_proceed.

NO enforcement, NO outbound, NO behavior change — this only writes ledger rows.
"""
import os
import sys

import psycopg
from dotenv import load_dotenv

ROOT = os.path.join(os.path.dirname(__file__), "..")
load_dotenv(os.path.join(ROOT, ".env"))

# One dict per live thread. Column names match migration 022. Honest state.
OPPORTUNITIES = [
    {
        "slug": "shipforge",
        "name": "shipforge — conversational site cloner + manager (flagship)",
        "stage": "scoped",
        "value_model": "outcome-billed",
        "est_value": None,
        "currency": "SGD",
        "next_action": "Close the render leak (auth/entitlement gate) + wire Stripe to the "
                       "outcome-billed model, then dogfood a Musa site -> first content client.",
        "next_action_owner": "cc-shipforge / Desmond",
        "due": None,
        "owner": "operator",
        "blocker": "Revenue leak: clean /r/...html render is publicly grabbable -> no reason to "
                   "pay. Stripe still wired to the dead 5-direction unlock, not the outcome-billed model.",
        "operator_is_blocker": False,
        "gate_status": "none",
        "gate_note": None,
        "partner": "Desmond Shen (build + deploy authority)",
        "sort_priority": 2,
        "notes": "Built ~80%; MVP live at shipforge.vercel.app. Model approved 2026-06-30: "
                 "clone-first -> unlimited fluid redesign -> outcome-billed (deliverable + hosting + "
                 "per-site monthly mgmt fee). One blocker from billable.",
    },
    {
        "slug": "storefront-merchants",
        "name": "storefront + merchants (single-brand F&B platform, Hadi etc.)",
        "stage": "scoped",
        "value_model": "saas",
        "est_value": None,
        "currency": "SGD",
        "next_action": "Land first paying merchant + agree the payment-cut structure (SaaS + a cut "
                       "of PayNow/Xendit payments processed).",
        "next_action_owner": "cc-storefront",
        "due": None,
        "owner": "operator",
        "blocker": "Merchant-onboard TG identity-bridge (075/077 apply+wiring) + PayNow "
                   "semi-auto verification. Customer-buy works today on a seeded store.",
        "operator_is_blocker": False,
        "gate_status": "none",
        "gate_note": None,
        "partner": None,
        "sort_priority": 4,
        "notes": "Product live on a seeded store; first real merchant is the operator's home-based "
                 "F&B friend. Recurring SaaS + payment cut. Storefront = shipforge's commerce engine.",
    },
    {
        "slug": "gazzabyte-partner",
        "name": "Gazzabyte (partner/reseller channel + irsyad tabung)",
        "stage": "closing",
        "value_model": "retainer",
        "est_value": None,
        "currency": "SGD",
        "next_action": "Draft + agree the partner payment structure (per-project / monthly retainer / "
                       "rev-share on their madrasah contract) — the clearest near-term close.",
        "next_action_owner": "Nazim (draft) -> operator (decide terms)",
        "due": "2026-09-30",
        "owner": "operator",
        "blocker": "Operator to decide the payment structure (his relationship, his call). Live "
                   "deliverables already in their hands (DMS report tweaks, per-denomination grid).",
        "operator_is_blocker": True,
        "gate_status": "open",
        "gate_note": "cai + operator gate on the GIRO/Tabung-Fajr money-audit sub-thread (financial "
                     "reconciliation data, residency check) — HARD end-Sept 2026 deadline.",
        "partner": "Gazzabyte (agency); Elly (end-client)",
        "sort_priority": 1,
        "notes": "Nearest-to-first-dollar close. Two sub-threads: (a) partner terms — non-money, draft "
                 "now; (b) Tabung Fajr inventory + GIRO/bank-statement upload — money/audit, cai-gated, "
                 "due end-Sept 2026 (Oct = Elly reconciles Jan-Sept). Be honest/direct: they're a partner.",
    },
    {
        "slug": "sushitei-fb",
        "name": "SushiTei — F&B full-suite vertical (via Gazzabyte, B2B2B)",
        "stage": "scoped",
        "value_model": "outcome-billed",
        "est_value": None,
        "currency": "SGD",
        "next_action": "Scope + price the Phase-0 secure beachhead off the hacked stack; lead with "
                       "security-as-architecture and beat Epicware (ours 91/1.3s vs their 55/32s).",
        "next_action_owner": "Nazim / cc-shipforge",
        "due": None,
        "owner": "operator",
        "blocker": "Awaiting operator steer on urgency + Gazzabyte scoping. The hack is the buying "
                   "trigger; Phase 0 has genuine urgency (active breach).",
        "operator_is_blocker": True,
        "gate_status": "none",
        "gate_note": None,
        "partner": "Gazzabyte (reseller) -> SushiTei (14-outlet chain)",
        "sort_priority": 5,
        "notes": "Roadmap/pitch drafted; ~60% exists as live product (shipforge + storefront + "
                 "branditqr + ihsanos substrate). Incumbent = Epicware (beatable, hard perf data). "
                 "The forcing function for the shipforge<->storefront wiring. Repeatable across "
                 "Gazzabyte's restaurant book.",
    },
    {
        "slug": "alderei-cosem-ar-pwa",
        "name": "Alderei / COSEM invoice-tracking AR-PWA (via Nahar/ADCDA)",
        "stage": "scoped",
        "value_model": "saas",
        "est_value": None,
        "currency": "SGD",
        "next_action": "Ship Phase 1 (5 additive gaps on the existing ihsanos invoicing module) -> "
                       "Phase 2 white-label PWA for Alderei; synthetic prototype -> real COSEM data.",
        "next_action_owner": "cc-ihsanos",
        "due": None,
        "owner": "operator",
        "blocker": "5 small gaps + a thin white-label PWA. ~75-80% already built (ihsanos invoicing "
                   "module has AR aging + PDF import).",
        "operator_is_blocker": False,
        "gate_status": "cleared",
        "gate_note": "Residency RESOLVED -> SG (COSEM commercial AR data, SG-resident). Still cai-ratify "
                     "the ceayj RLS-isolation before the first real COSEM invoice. Synthetic-first.",
        "partner": "Nahar (ADCDA POC) / Major Alderei (payer-side viewer)",
        "sort_priority": 6,
        "notes": "NOT a new `daftar` repo — extend the existing ihsanos invoicing module. Modular: "
                 "Alderei = tenant #1, next client = config. Reusable invoice/AR-tracking vertical.",
    },
    {
        "slug": "cosem-adcda-ray-ai",
        "name": "cosem / ADCDA + Ray-AI course-administrator agent",
        "stage": "qualified",
        "value_model": "saas",
        "est_value": None,
        "currency": "SGD",
        "next_action": "Decide: is Ray-AI a SOLD product (COSEM/ADCDA pays for the CA agent) or an "
                       "internal dogfood? + deliver the ADCDA course-video (due 2026-07-31).",
        "next_action_owner": "Nazim / operator",
        "due": "2026-07-31",
        "owner": "operator",
        "blocker": "Operator's go on the first step (stand up the agent + bot, wire Drive ingest, "
                   "produce this week's Director one-pager as proof).",
        "operator_is_blocker": True,
        "gate_status": "cleared",
        "gate_note": "Shared 'Ray' Drive folder is CLEAN of trainee Military IDs -> can prototype now. "
                     "Gov-PII silo gate only bites if parent namelists are later ingested.",
        "partner": "Syed Nurrahman Shah (Ray) — COSEM Team B / ADCDA CA",
        "sort_priority": 7,
        "notes": "Ray-AI proposal + corpus ingestion delivered 2026-07-21; dogfood-queued. Concrete "
                 "first instance of the 'per-project dedicated bot' scaling idea. Plus the operator's "
                 "invited 'ADCDA beyond current functionality' bigger play.",
    },
    {
        "slug": "uae-sea-shariah-rails",
        "name": "UAE->SEA Shariah-compliant payment rails (Hafiz) — north star",
        "stage": "lead",
        "value_model": "saas",
        "est_value": None,
        "currency": "SGD",
        "next_action": "Run the GTM sequence: free native PayNow-QRIS linkage NOW (prove volume) -> "
                       "build the ERP/orchestration moat -> approach Thunes/Nium with real volume. "
                       "Hafiz scoping sprint (90 min) to define pipeline v1.",
        "next_action_owner": "operator + Hafiz (Nazim facilitates)",
        "due": None,
        "owner": "operator",
        "blocker": "Longest horizon, biggest prize. Commercial terms with Hafiz + any money movement "
                   "are operator + cai. Building blocks exist; needs the regulatory/product judgment layer.",
        "operator_is_blocker": True,
        "gate_status": "open",
        "gate_note": "cai-led regulatory scoping FIRST — cross-border money = heavy licensing/AML. Model "
                     "is ujrah (fee-for-service), NOT riba; FX spot not speculative; ride a licensed "
                     "umbrella (Thunes/FAB), don't become a PSP.",
        "partner": "Hafiz (ex-DBS PayLah! AVP, NDA-gated); Thunes/Nium (rails)",
        "sort_priority": 8,
        "notes": "Operator's crystallized fintech north star (2026-07-16). Hafiz brief sent; "
                 "share.wingmen.dev/r/hafiz-brief. Capital-light SaaS/ERP riding LICENSED rails. "
                 "Halal-by-construction is the moat.",
    },
    {
        "slug": "desmond-shen-channel",
        "name": "Desmond Shen — scoped partner / delivery channel (shipforge + storefront)",
        "stage": "channel",
        "value_model": "partner",
        "est_value": None,
        "currency": "SGD",
        "next_action": "Track the shipforge/storefront clients he brings + ships; his green light = "
                       "deploy authority on those two workstreams.",
        "next_action_owner": "cc-orch (track)",
        "due": None,
        "owner": "operator",
        "blocker": "Not a sale — a channel/partner. Revenue shows up as the shipforge/storefront "
                   "clients his workstreams convert.",
        "operator_is_blocker": False,
        "gate_status": "n/a",
        "gate_note": "Money-path, prod-mutation on OTHER repos, and governance still route to operator "
                     "+ cai. shipforge/storefront deploys are Shen's call.",
        "partner": "Desmond Shen (@Alexisonfires)",
        "sort_priority": 9,
        "notes": "Scoped access granted 2026-06-26 (group -5319479270). Full build/dispatch + deploy "
                 "authority on shipforge + storefront only. Tracked as a channel, not a lead.",
    },
]

COLUMNS = [
    "slug", "name", "stage", "value_model", "est_value", "currency",
    "next_action", "next_action_owner", "due", "owner", "blocker",
    "operator_is_blocker", "gate_status", "gate_note", "partner",
    "sort_priority", "notes",
]


def main() -> int:
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        print("ERROR: DATABASE_URL / SUPABASE_DB_URL not set", file=sys.stderr)
        return 1
    placeholders = ", ".join(["%s"] * len(COLUMNS))
    collist = ", ".join(COLUMNS)
    insert = (
        f"INSERT INTO opportunities ({collist}) VALUES ({placeholders}) "
        f"ON CONFLICT (slug) DO NOTHING"
    )
    inserted = 0
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        for row in OPPORTUNITIES:
            cur.execute(insert, [row[c] for c in COLUMNS])
            inserted += cur.rowcount
        conn.commit()
        cur.execute("SELECT count(*) FROM opportunities")
        total = cur.fetchone()[0]
    print(f"Seed complete: {inserted} new row(s) inserted; {total} total in the ledger.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
