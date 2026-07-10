# REVIEW BRIEF — 089 relocate bank_reference + close-gate (money-path, CAI-RESP-412)

You are **cc-reviewer**, INDEPENDENT read-only money-path review. Client testing LIVE on the irsyad silo goumlyne `goumlynecruxrlmzlntp`. Be adversarial — find what breaks, do not rubber-stamp. Do NOT edit/commit/apply/merge.

Workspace: `~/wingmen/projects/ihsanos-irsyad` (separate checkout — do NOT touch `~/wingmen/projects/ihsanos`). `git fetch origin`, then review `git diff origin/main...origin/feat/tabung-bankref-relocate` (@ b651dc6).

## Context
cai cleared the DESIGN in **CAI-RESP-412** (read: `SELECT * FROM get_decision('CAI-RESP-412')`; parent CAI-RESP-404). Migration **089_tabung_bankref_relocate.sql**. cc-ihsanos reports 8/8 DB-proofs on ceayj pooled (rolled back, live goumlyne untouched), work_output #259. The design intent: the client banks in BATCHES and the deposit reference only exists AFTER the physical deposit — so per-tin `bank_reference` at mark-banked time hard-blocks the cashier. The fix relaxes the per-tin requirement BUT must NOT open an audit hole (banked money with no deposit proof). The close-gate is the compensating control.

## THE ONE QUESTION THAT MATTERS MOST
**Can banked money reach a terminal-closed report WITHOUT a real deposit_reference + slip?** Hunt every path around the close-gate. If you find one, that's a CHANGES-REQUIRED blocker — the whole point of 089 is that (a) nullable does not ship without (c) close-gate actually blocking.

## Verify (each must hold — report rowcount/error evidence, not the PR's word)
1. **bank_reference nullable at mark-banked** — markBanked SUCCEEDS with NULL ref (counted→banked). Confirm ALL THREE NOT-NULL enforcements were relaxed: (i) the 088 tin bank-guard trigger raise, (ii) the `tabung_mark_banked_atomic` RPC raise, (iii) the `tin_banked_has_metadata` CHECK from 056/057. Confirm no FOURTH path still silently requires it. Confirm relaxing the 056/057 CHECK did NOT weaken the OTHER metadata it guards (amount, attribution must STILL be required — only bank_reference relaxed).
2. **All other 404 guards preserved verbatim** — FROM-state pin (counted→banked only; no any→banked, no un-bank, no re-bank), own-org, actor role ∈ {preparer, org_admin}, GUC arming, and the immutability freeze on bank_reference/amount/attribution once set (a tin that DID get a ref still cannot have it overwritten). No cashier silently gains bank/sign. No RLS-helper SECDEF fn revoked (CAI-RESP-303).
3. **Report-level deposit_reference + deposit_slip_url** on tabung_weekly_reports — WRITE-ONCE (trigger blocks any change once set), and the `tabung_report_attach_deposit` RPC is properly gated (preparer|org_admin, own-org, status ∈ {draft, preparer_signed}, service_role only). No placeholder-then-correct path.
4. **CLOSE-GATE — DB-enforced in `081` `tabung_endorse_close_report`** — a report covering ANY banked tin CANNOT reach terminal close without deposit_reference + slip (`tabung_deposit_reference_required_to_close`). CRITICAL: confirm the gate covers EVERY banked tin in the report snapshot, not merely "the report has some reference." Confirm it's in the SECDEF close fn (not UI-only) and can't be bypassed by a raw UPDATE or the shared writer path.
5. **Reconciliation surface `tabung_banked_awaiting_deposit(org, threshold_days)`** — lists banked tins with no covering report deposit_reference past threshold; drops once the report gets its ref. Confirm it doesn't leak cross-org (own-org scoping).
6. **Migration 089** idempotent; direct psycopg-apply pattern (NOT `supabase db push` vs prod); `--expect-ref` guard present.
7. **Re-run** the 8 DB-proofs yourself against pooled (they roll back) — confirm 8/8 and that they do NOT touch live goumlyne.

## Output
Post an `agent_messages` decision to `to_agent='cc-orchestrator'`: **APPROVE** (money-gate clear) or **CHANGES-REQUIRED** (exact defects + evidence). Advisory to cai (you review, cai rules). Do NOT apply/merge to live.
