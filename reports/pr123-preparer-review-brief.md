# REVIEW BRIEF — PR #123 tabung preparer role (money-path, CAI-RESP-404)

You are **cc-reviewer**, doing an INDEPENDENT money-path review. This is the cc-reviewer sign-off gate for a client-testing-LIVE feature. Be adversarial: your job is to find what breaks, not to rubber-stamp.

Workspace: `~/wingmen/projects/ihsanos-irsyad` (a separate ihsanos checkout — do NOT touch `~/wingmen/projects/ihsanos`, cc-ihsanos is actively working there). Branch under review: **`feat/tabung-preparer-role`** @ `cb2e74ad`, vs `origin/main`. `git fetch origin` then `git diff origin/main...origin/feat/tabung-preparer-role`.

## Context
cai cleared the DESIGN in CAI-RESP-404 (read: `SELECT * FROM get_decision('CAI-RESP-404')`). cc-ihsanos built it: migration **088**, DB-proofs 12/12 green on ceayj pooled (rolled back, live goumlyne untouched) per work_output #248. Reproduce: `scripts/db/verify-088-preparer-role.py`.

## Verify (each must hold — report rowcount/error evidence, don't take the PR's word)
1. **Distinct named `preparer` role** — a NEW role, NOT an extension of `cashier`. No cashier silently gains bank/sign. Generic name (not elly/gazzabyte).
2. **markBanked = guarded monotonic transition** at the DB: actor ∈ {preparer, org_admin}; own-org only; `old.status='counted' AND new.status='banked'` ONLY (no any→banked, no banked→* — no un-bank/re-bank/ref-overwrite); `bank_reference` NOT NULL. Confirm it's DB-enforced (RLS policy pinning FROM-state or SECURITY DEFINER fn), not UI-only. Confirm no RLS-helper SECDEF fn was revoked (CAI-RESP-303).
3. **preparer ≠ endorser** enforced at the DB in the same txn (CHECK/trigger): `endorser_person_id <> preparer_person_id` AND endorse requires org_admin.
4. **Migration 088** idempotent, uses direct psycopg-apply pattern (NOT `supabase db push` vs prod).
5. **Re-run** `scripts/db/verify-088-preparer-role.py` yourself against pooled (it rolls back) — confirm 12/12, and that it does NOT touch the live goumlyne silo.
6. Corrections still gated to org_admin (task #6/FLAG-B not weakened).

## Output
Post an `agent_messages` decision to `to_agent='cc-orchestrator'`: **APPROVE** (sign-off, money-gate clear) or **CHANGES-REQUIRED** (list exact defects with evidence). Do NOT apply anything to live. Do NOT merge. The red irsyad-frs E2E is known pre-existing seed drift (cc-ihsanos fixing separately) — don't block on it unless you find it's actually caused by this PR.
