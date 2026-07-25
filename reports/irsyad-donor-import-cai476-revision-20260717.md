# Donor-import REVISION brief — CAI-RESP-476 (grant WITHHELD on sha 5f4504b)

**To:** cc-ihsanos donor-import builder (worktree `~/wingmen/ihsanos-wt/donor-import`, branch `chore/irsyad-donor-import`)
**From:** cc-orchestrator (hub)
**Gate:** CAI-RESP-474 → cai withheld grant in CAI-RESP-476. Money discipline was CREDITED (exact $1,718,341.46 reconcile, per-Type, 26 $0-skips, idempotency, crypto, cc-reviewer APPROVE). Two REQUIRED changes + one provenance confirm before re-submit. **Same posture: AUTHORED + DRY-RUN ONLY, do NOT apply** (hub applies after cai flips grant on the new sha).

## REQUIRED CHANGE 1 — DROP `address` entirely
- Do **not** write `persons.address` at all. Remove `address` from the persons INSERT and from the plan builder (`_clean(r[iStreet])` mapping).
- Rationale (cai): plaintext minors'-family home addresses on a `persons` table that `anon` holds `GRANT ALL` on (RLS-only) is an unacceptable latent exposure over 2,613 sensitive rows; and no shipped feature consumes donor address. There is NO `address_encrypted` column — do NOT add a migration for it now. Minimize-PII: drop it.
- Money/name/encrypted-phone/encrypted-email import proceeds unchanged; **reconcile is unaffected** ($1,718,341.46 must still be EXACT).
- Update `docs/clients/irsyad/donor-import-mapping.md`: Street → **DROPPED per CAI-476** (not imported).

## REQUIRED CHANGE 2 — bucket the 113 non-numeric-ID rows; do NOT create 113 fake persons
- The `ID` column is meant to be numeric. The 113 non-numeric-ID rows are aggregate/label rows (e.g. `Jan to Apr 2024`, `Tabung Jumaat 2025`) — **aggregate donations, not donors**. 113 junk persons commingled with real minors'-families would corrupt donor-facing features/receipts.
- Attribute ALL those amounts to **ONE** clearly-labeled aggregate donor person, e.g. display_name `Aggregate / Unattributed — 2020–2026 Excel import`, with a deterministic uuid5 key (stable, idempotent) and a `custom_fields`/`tags` marker identifying it as the aggregate bucket. The donation rows still exist (so the **total still reconciles EXACTLY to $1,718,341.46**), but `persons` gets **1** aggregate record instead of 113.
  - Acceptable fallback if single-bucket is impractical: mark those donations with a distinct **non-person** aggregate marker (e.g. `is_anonymous`/tag) so they're queryable-as-aggregate and never mistaken for real donors — but **not** 113 plain person rows.
- **Enumerate the 113** distinct non-numeric IDs in the mapping doc for auditability (so a human can spot any that are actually a real donor-name-as-ID; default treatment = aggregate).
- Per-Type sums and grand total must stay EXACT after re-bucketing.

## CONFIRM 3 — created_by provenance (cai: ACCEPTABLE with the marker)
- `created_by = org_admin 610e1574` is accepted ONLY because `import_ref`/uuid5 make bulk-import provenance explicit. **Strengthen it:** ensure the provenance is unmistakable in the data — put file name (`Collected Tabung 2020 to 2026.xlsx`), the applier commit sha, and import date into the donation/person `custom_fields` (or an equivalent audit note) so the audit trail cannot read as that admin hand-keying 2,613 rows. Confirm this in the report.

## ACCEPTED (no change) — payment_method='cash' (physical tabung tins).

## POST-PROOF hardening cai requires (bake into the applier now)
- **Zero-mutation must be DB-WIDE, not just org-scoped.** goumlyne currently holds exactly ONE pre-existing donation and it belongs to a DIFFERENT org (`eaa0ef4a-82ce-4ce2-8b3c-a59fe992e77d`, $108.75) — the irsyad org (73339164) pre-count is **0** (assert-empty correct). Prove that foreign row's sum/count is unchanged (DB-wide snapshot before/after), not only the org-scoped zero-mutation. The applier's org-scoped assert-empty stays; ADD a DB-wide zero-mutation assertion.
- Keep the anon-denied RLS proof; note that post-**apply** (hub) it must be re-run on the REAL imported rows (not just dry-run). Applier should already support re-proving on committed rows.
- Optional (cai H3): make assert-empty hard-abort if `pre_batch > 0` unexpectedly (still additive-safe either way).

## Deliverable
- Commit on `chore/irsyad-donor-import` → **new sha**. Re-run the dry-run against goumlyne (`--dry-run --allow-placeholder-key`), confirm ALL proofs PASS incl DB-wide zero-mutation and the EXACT $1,718,341.46 reconcile with 1 aggregate person (not 113). Report the new sha + full dry-run proof to hub (cc-orchestrator). **Do NOT push, do NOT apply.**
- Then: hub re-verify → fresh cc-reviewer pass → re-package to cai → cai re-verify (sample+RLS) → grant → hub applies with real `NRIC_ENCRYPTION_KEY` + `--expect-ref goumlynecruxrlmzlntp` + real-row post-proof.
