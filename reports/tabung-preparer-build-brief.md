# BUILD BRIEF — Tabung Preparer Role (per CAI-RESP-404)

You are **cc-ihsanos**, building in `~/wingmen/projects/ihsanos` (the ihsanos APP repo). This is a **client-requested, cai-cleared, operator-approved** feature for the irsyad/Gazzabyte tenant, who is **testing LIVE right now** — so build on a branch, prove it with tests, and DO NOT apply anything to the live goumlyne silo yourself.

## What & why
Cashier **Elly** must be able to count + **mark tins banked** + **sign the weekly report as preparer (1st signature)** — WITHOUT being a full admin. Admin (Saddam) stays the endorser (2nd signature) + retains all admin powers. This is a maker-checker split.

## The cleared design (CAI-RESP-404 — read the full ruling: `SELECT * FROM get_decision('CAI-RESP-404')` on the substrate; and the money-gate origin CAI-RESP-393/402)
1. **Distinct named `preparer` role** — a NEW generic role (NOT `elly`/`gazzabyte`-named), assigned per-person to Elly. Do NOT extend the shared `cashier` grant-set (that would silently give every cashier bank+sign). Roles live in `PersonRole`/`role` enum (`src/shared/types/index.ts` line ~80). Gate at the DB grant/RLS layer, not UI-only.
2. **markBanked = guarded MONOTONIC transition**, not a blanket UPDATE grant: actor role ∈ {preparer, org_admin}; own-org only; FROM-state pinned `old.status='counted' AND new.status='banked'` (never any→banked, never banked→* — so no un-bank, no re-bank, no bank_reference overwrite); `new.bank_reference` NOT NULL. Corrections stay org_admin's amend path (task #6/FLAG-B). Implement via an RLS policy pinning the FROM-state OR a SECURITY DEFINER fn (follow the existing `post_journal_atomic` pattern in the repo). Respect CAI-RESP-303: never revoke RLS-helper SECDEF funcs.
3. **preparer ≠ endorser, enforced at the DB in the SAME txn** (CHECK constraint or trigger, NOT UI-only): `endorser_person_id <> preparer_person_id` AND endorse requires org_admin.

## Deliverables (this is the money-gate bar — all required before deploy)
- **Named migration** (idempotent; use the orch's direct psycopg-apply pattern per PR #41/#42/#44 — NEVER `supabase db push` against prod, per CLAUDE.md).
- **DB-proof test artifacts** — per CAI-RESP-402 bar, show **rowcount + error-text for each case** (no self-close on claim): preparer CAN counted→banked own-org; preparer CANNOT endorse / amend / cross-org / re-bank; endorser==preparer REJECTED at DB; org_admin retains all. Write these to Supabase `work_outputs` (repo files alone are insufficient, per CLAUDE.md).
- **App layer**: surface markBanked + report preparer-signature to the `preparer` role in the counter/report UI (`src/app/dashboard/tabung/...`), matching where org_admin sees them today.
- Branch off the correct base (the tabung denomination-count work must be present — check whether it's on `origin/main` yet; if not, base off `feat/tabung-denomination-count`). Push, open a PR, and route it to **cc-reviewer** (independent money-path review).

## Gate before ANY live apply (do NOT do these yourself)
DB-proofs pass → cc-reviewer sign-off → cc-orchestrator re-confirms the final permission list with the client → THEN apply. The **24h cooldown is WAIVED** by the operator (consensus feature), but every other gate stands.

## Report to cc-orchestrator (`agent_messages`, to_agent='cc-orchestrator')
When the branch + migration + DB-proofs + PR are ready. Flag blockers early. Related queued tabung tasks (do the preparer role FIRST): report-format match to their TABUNG FAJAR PDF (2 sigs Prepared/Endorsed + a remarks/adjustment field), slip-attachment on the report, split-banking (notes weekly/coins quarterly). One thing at a time, FIFO.
