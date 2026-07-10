# BUILD BRIEF — relocate bank_reference off per-tin onto weekly report + close-gate (CAI-RESP-412)

You are **cc-ihsanos**. Money-path build, client testing LIVE (irsyad silo goumlyne `goumlynecruxrlmzlntp`). DESIGN is cleared by cai in **CAI-RESP-412** (read it: `SELECT * FROM get_decision('CAI-RESP-412')`; parent CAI-RESP-404). Build + DB-prove ONLY — **do NOT apply to live**; apply is gated on cai's §6.6 grant (ceayj-first, then goumlyne).

## Why
CAI-RESP-404 made `bank_reference` NOT NULL + immutable **per-tin at mark-banked time**. Real client flow is a BATCH deposit — the deposit reference only exists AFTER Elly physically banks, at the report/batch level, not per tin. So the current field HARD-BLOCKS her (117 tins). Fix relocates the reference to where it truly exists and moves the audit teeth to report-close. Naive "just nullable" is REJECTED (opens a banked-money-with-no-proof hole).

## Ship as ONE coherent migration (call it 089). Minimum unit = (a)+(c)+(d) TOGETHER — never (a) without (c).

### (a) TIN LEVEL — bank_reference becomes NULLABLE / not-required at mark-banked
- In `088`'s tin bank-guard trigger, the `tabung_bank_reference_required` raise (currently: `IF NEW.bank_reference IS NULL OR btrim(...)='' THEN RAISE 'tabung_bank_reference_required'`, ~line 184) — **remove the NOT-NULL requirement**.
- In RPC `tabung_mark_banked_atomic` (`088`, ~line 262/277) — make `p_bank_reference` **optional** (drop the `bank_reference_required` raise; allow NULL). Column stays TEXT NULL.
- **KEEP every other 404 guard intact**: FROM-state pin (`old.status='counted' AND new.status='banked'` only, no any→banked, no un-bank/re-bank), own-org, role ∈ {preparer, org_admin}, the `tabung_bank_reference_immutable` trigger (~line 211 — once set, frozen; a tin that DOES get a ref still can't have it overwritten), the mark_banked GUC arming. Only the NULL-check relaxes.

### (b) REPORT LEVEL — deposit_reference + attached slip on the weekly report (write-once immutable)
- Add to `tabung_weekly_reports`: `deposit_reference TEXT NULL`, and slip attachment (reuse the pattern task #15 slip-attachment will use; if #15 storage isn't merged yet, at minimum add `deposit_slip_url TEXT NULL` / attachment ref column so the close-gate has something to assert).
- **Write-once → immutable once set** (trigger, mirror the 088 immutability pattern): once `deposit_reference` is non-null it can't be changed. No placeholder-then-correct — you never write it until the real reference exists.
- **v1 = the weekly report IS the batch.** Do NOT build a separate batch entity (anti-takalluf per cai). Generalize only if they later bank multiple batches per report.

### (c) CLOSE-GATE INVARIANT (the audit teeth — MANDATORY, ships WITH (a)) — DB-enforced in `081`'s `tabung_endorse_close_report`
- Before the report transitions to closed/terminal (in `tabung_endorse_close_report(p_report_public_id, p_endorser_id)`, `081`): assert the report carries a non-null `deposit_reference` + attached slip **AND** that it covers every banked tin in the report's snapshot. If any banked tin is not covered by a real deposit reference → `RAISE EXCEPTION 'tabung_deposit_reference_required_to_close'`.
- Result: no banked money reaches terminal-closed without real deposit proof. This IS 404's audit intent, moved to the correct point. Must be DB-enforced (in the SECDEF close fn), not UI-only.

### (d) RECONCILIATION SURFACE — banked-awaiting-proof visible + flagged
- Reuse the CAI-RESP-405 anti-iktinaz held/velocity surface (find it — likely a view from 080/082 money-tail). A tin `status='banked'` with no report-level `deposit_reference` past a threshold surfaces for reconciliation. This is the guard that keeps "optional at bank time" from silently becoming "never."

## DB-PROOF (behavioral — this is the money-gate evidence, write to work_outputs)
Prove on **ceayj pooled** (roll back; do NOT touch live goumlyne), report rowcount/error per case:
1. markBanked SUCCEEDS with `bank_reference=NULL` (tin goes counted→banked, all other guards still hold).
2. Un-bank / re-bank / any→banked still REJECTED (404 guards intact).
3. A tin that DID get a bank_reference still can't have it overwritten (immutable intact).
4. Report `tabung_endorse_close_report` **FAILS to close** when it has a banked tin and NO `deposit_reference` (`tabung_deposit_reference_required_to_close`).
5. Report **CLOSES** when `deposit_reference` + slip present and cover all banked tins.
6. `deposit_reference` is write-once — a second UPDATE changing it is REJECTED.
7. Reconciliation surface lists a banked+un-referenced tin; drops it once the report gets its reference.
8. Migration 089 idempotent; direct psycopg-apply pattern (NOT `supabase db push`), `--expect-ref` guard.

## Process
- Branch off `origin/main` (has 088 @ c5da5b0). New migration `089_tabung_bankref_relocate.sql`.
- When proofs are green: report to cc-orchestrator (work_output + agent_messages). Then cc-reviewer independent money-path review → cai §6.6 grant → ceayj-first apply → goumlyne apply → live-verify with Elly's identity → confirm to client.
- **Do NOT apply to live. Do NOT merge to main without the gate.** Build + prove in parallel NOW so it ships the instant the window clears / operator expedites.
