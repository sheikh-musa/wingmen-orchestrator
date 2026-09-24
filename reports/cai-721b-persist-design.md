# CAI-721b — Persist Unfinished Bank-Import Reviews — Design (Fable, 2026-08-05)

Client-escalated TOP priority (op#10558, banking-day urgency). Design-first → cai money-safety review → build. Grounded in sheikh-musa/ihsanos@main (bank-import-client.tsx, bank-import.ts, migrations 084/140/058/095/137, action-gate.ts, vercel.json cron).

## Key framing
The code ALREADY anticipates this by name — bank-import.ts L38-41 + client L62-64: "review state lives in browser memory between the two calls... when the review cadence stretches across sessions, we add a `bank_import_runs` table." That condition is now hit; this is that table.

## Data model — `bank_import_sessions` (one table, JSONB row-array)
Cols: id, org_id, status CHECK(in_progress|committed|discarded|expired), source_file_name, parse_variant CHECK(ocbc_xlsx|ocbc_csv|mif_csv), total_credit_rows, skipped_zero_credit, skipped_malformed, rows JSONB (array of PreviewRow⊕EditableRow — the parsed data + the user's partial edits: category_id_override, person_id_override, skip, notes), created_by, created_at, updated_at, last_saved_by, committed_at, discarded_at. (matched/unmatched counts NOT stored — reduce over rows, avoids drift.)
JSONB blob (not normalized child) because the client already treats rows:EditableRow[] as one atomic unit (setRows replaces whole array); row count bounded (~945-1900); smaller RLS surface for cai to audit.
Flows: saveBankImportSessionAction (insert then UPDATE...WHERE status='in_progress' — status guard makes save-after-commit a no-op), listBankImportSessionsAction (in_progress picker), getBankImportSessionAction (resume → set step='review' directly, skip upload+parse), commit (existing commitBankImportAction + optional sessionId → flip status='committed'), discard (soft). Soft-delete convention (no hard DELETE in RLS).

## MONEY-SAFETY (cai crux)
- NO DOUBLE-COMMIT: resume does NOT get its own write path into donations — it reconstructs the exact CommitRow[] payload and calls the UNMODIFIED commitBankImportAction, which re-derives contestedness/fallback from LIVE bank_keyword_mappings (never trusts stored suggested_category_reason) + checks uq_donations_import_ref (mig-084 partial unique index) before insert. Dedup is orthogonal to fresh-parse-vs-resumed.
- HIGHEST RISK (discipline-to-verify, not a design flaw): resume must NEVER add a shortcut that trusts a STORED decision instead of re-deriving live at commit. Every CAI-659/665/666/669/698 guard exists because a client claim was once trusted; the staging table is a 2nd place client-looking data lives — the "commit always re-derives from live mappings" discipline must hold identically. cai should check for this in the build review.
- STALENESS handled: commit re-fetches live mappings, so a stale session's rows re-evaluate against current rules — correct; but resumed UI should refresh badges on load, not trust stored snapshot.
- Idempotency+audit: save = UPSERT gated status='in_progress' (idempotent); audit_log entry per lifecycle transition (create/discard/commit, NOT per autosave).
- View-as guard inherited free (gateAction refuses full-access actions during view-as preview).
- RLS: auth_user_org_ids_with_module('bank_import','full') (mig-137 generic helper — more precise than role-array; a preparer without the bank_import grant is correctly excluded). Org resolution via getOrgContext (the CAI-734 multi-org fix — don't reinvent).
- RESIDENCY: goumlyne (Irsyad) + ceayj (schema symmetry) — house precedent (058/084/137).
- PII: counterparty_raw plaintext, org-scoped RLS, no field-encryption (matches mig-140 donations.counterparty_raw; encrypting breaks resume display). NEW: persisting payer names = a NEW retention commitment (was ephemeral memory) → TTL is the mitigation.
- TTL: cron EXISTS (vercel.json /api/cron/* w/ CRON_SECRET). New /api/cron/bank-import-sessions-purge daily: (1) in_progress >Nd (30-60?) → 'expired' (off resume list, admin-recoverable); (2) expired >Md → hard-delete the rows JSONB (tombstone kept). The actual PII-bound step.

## UX
Explicit "Save & exit" (not silent autosave in v1); resume entry on the bank-import page (list in_progress sessions above the dropzone); resumed session = identical review table entered via load-from-DB; distinct Discard vs Cancel affordance. Badges refresh vs live mappings on load.

## Effort M — phasing
(1) table+RLS+save/list/resume/discard actions + minimal UI (ships the client's ask). (2) TTL cron + expiry UX. (3) optional: extend to the Stripe rail (same PreviewRow/CommitRow plumbing).

## Open questions (cai/operator/client)
1. TTL window (30d→expired, +30d→purge — OK given ≤weekly cadence?).
2. Autosave vs explicit-save-only (v1 = explicit).
3. Single global in_progress session per org vs multiple concurrent (design allows multiple; partial-unique-index if "one at a time").
4. Concurrent-edit = last-write-wins (no optimistic lock) — accepted risk?
5. Stripe rail in scope or v1-defer?
6. Table name bank_import_sessions vs the code's anticipated bank_import_runs.
