# #508 tin-serial import link — reworked mig253 APPLIED + 114 links EXECUTED (COMPLETE)

**Owner:** Nazim / orch-console · **Date:** 2026-08-25 · **Silo:** goumlyne (irsyad org `73339164`), schema applied both silos
**Authorization:** Wan (client) op#16378/16379 (link the serials); console ruling 33333/33291 (block is spurious); irsyad money authority op#16167 (cai out of irsyad → Musa-transparency, not cai-gate).

## What changed
Reworked mig253 (`link_tin_donation`) — the **active-issued-receipt block was removed** (console ruling 33333: spurious for this RPC — receipts carry no serial column, no receipt/letter render reads the serial or the side table, and the RPC never touches the donation/receipt row). Every other guard is byte-identical.

## Independent gate (Nazim, at source — not on coord's recap)
Ran my own wet-prove `scratchpad/wetprove_console_253.py` (CAI-756-safe: BEGIN;/COMMIT; stripped, wrapped in my own txn, ROLLBACK for dry-run):

**goumlyne dry-run — 13/13 PASS:**
- fn-body diff (pg_get_functiondef pre→post, normalized): removes **ONLY** the receipt-immutability block (13 lines, all part of that block); **0 added logic lines**.
- proACL: anon=EXECUTE:False, authenticated=True, service_role=False.
- RLS: enabled on `tabung_serial_import_log`; SELECT policy present + org_admin-scoped.
- All 63 target donations carry an active issued receipt (the block **would** have fired → the exact blocker being fixed).
- **All 114 links SUCCEEDED (114/114).**
- **Every receipt row byte-identical before==after** (receipt immutability empirically intact — proves the block was truly spurious).
- Exactly 114 audit_log rows appended; 114 side-table rows.
- Floors: 63/63 donations tabung-category; **0 serials collide with kk tins** (minors belt); 0 pre-existing links (fresh).

**ceayj schema-parity — 7/7 PASS:** diff removes only the receipt block, proACL/RLS parity, org_admin policy present. **Cross-silo fn byte-parity: goumlyne post == ceayj post (identical).**

## Apply + execute (committed)
- **mig253 rework applied + committed BOTH silos** (goumlyne + ceayj), receipt block confirmed gone post-apply, proACL re-verified.
- **114 links executed + committed on goumlyne** (one atomic txn) as SERVICE admin `orchestrator@ihsanos.com` (`ca2a4a9c-f745-40c3-9536-7a021fd42bb9`, org_admin of org 73339164 — verified at source). Params: `p_expected_existing_link=NULL`, `import_source='wan-tabung-return-fajar-kedai-20260824'`, `match_method='amount_date_exact'`, `match_confidence='high'`, canonical-json audit payload.

## Post-commit verification (fresh connection, at source)
- 114 persisted rows across 63 distinct donations; **exact match to the authoritative `tin-serial-links-final.json`**.
- 114 audit rows; **hash-chain re-verified: 0 hash mismatches, 0 linkage breaks** (recomputed sha256(prev‖canonical-payload) from stored fields).
- Receipts: 63 donations still active issued (untouched).

## Held (need Wan) — NOT linked
1. **serial 25266** — appears on TWO Zarina Sidik rows 2026-02-01 ($281.85 / $457.66); ambiguous, Wan must pick.
2. **row 71 Puan Siami $467 "Own Tabung"** — no serial.

## Next
- ~102 unmatched sheet rows → digest list for Wan (follow-up deliverable).
- Letter-wire fix (repoint resolver → side table + Design-B DEFINER read) — GO'd (33383), unblocked now that links exist; PR routes to me for the 4-part ship-gate.
