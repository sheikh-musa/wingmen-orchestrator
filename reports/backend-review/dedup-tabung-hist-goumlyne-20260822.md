# Dedup — irsyad tabung-history per-donation person split (goumlyne)

**Date:** 2026-08-22 · **Owner:** Nazim (orch-console) · **Authorization:** Musa op#15902 "proceed" (+ op#15900 "fix the duplicates and leave the tool"), name-alone caveat raised & accepted (op#15901) · **Silo:** goumlyne `goumlynecruxrlmzlntp`, org `73339164-7c1f-40ba-a093-33f1f292dd4c` · **Actor:** orchestrator@ihsanos.com (`ca2a4a9c-f745-40c3-9536-7a021fd42bb9`, our service org_admin)

## Root cause
The `irsyad_tabung_hist_2020_2026` import (created 2026-07-23, 1596 person rows, each with a per-DONATION `custom_fields.import_identity`=`R::TABHIST-<hash>`) minted **one person per donation instead of per donor**. A donor with N gifts became N person rows (e.g. `encik muhd rashid ( f&n office)` = 39 rows). A separate 2026-07-05 batch (1459 rows, no import metadata, unique names, addresses on some) is the donor **master list**.

No `nric_hash` (0), no `date_of_birth` (0), no `user_id` (0) anywhere; address only 666/3189, phone 394, email 629, none corroborating cross-batch. The tabung source identified donors **only by name string** → collapsing can only key on exact name. Surfaced to Musa (conflict with "name+address, never name-alone"); he approved exact-name for SPECIFIC strings + holdback.

## Method
- Grouped active persons (`merged_into IS NULL AND deleted_at IS NULL`) by `lower(btrim(display_name))`.
- **SAFE subset merged:** clusters with name **≥2 tokens AND ≤1 distinct address** (473 clusters).
- Survivor = row **with an address** first, else non-hist (master), else oldest/lowest-id → cleaned donor keeps richest profile.
- Merge via `merge_persons()` SECDEF (mig178 + mig221 drift-guard): reparents all person-FKs incl `donations.person_id`, soft-deletes loser (`merged_into`=survivor), writes `person_merge_snapshot` (reversible via `reverse_merge()`) + hash-chained `audit_log`. `auth.uid()` set via `request.jwt.claim.sub`=actor per-transaction; committed per-cluster.
- **WET-PROVEN on ROLLBACK first** (3 largest clusters): donations reparented losslessly, losers soft-deleted, snapshots written, rollback fully restored.

## Result (verified at source) — TWO phases
**Phase 1** (op#15902): safe subset (name ≥2 tokens, ≤1 distinct address).
**Phase 2** (op#15908): the address-"conflict" clusters that were the SAME donor with the address recorded at two completeness/formatting levels (e.g. `Pasir Ris St 21` vs `BLK 216 Pasir Ris St 21 #03-194 S(510216)`). Same-place test = shared postal `S(######)` OR one normalized address contained in the other; survivor = fullest address. Script: `scratchpad/dedup_addrconflict_phase2.py`.

| metric | Phase 1 | Phase 2 | total |
|---|---|---|---|
| merges committed (0 failed) | 1283 | 364 | **1647** |
| donors after collapse | 473 | 63 | — |
| active persons | 3186 → 1903 | 1903 → **1539** | −1647 |
| live donations after | — | — | 2821, sum **$1,074,658** (unchanged) |
| donations orphaned on soft-deleted person | 0 | 0 | **0** |
| reversible snapshots (orchestrator, unreversed) | — | — | **1647** |

`merge_persons` never deletes donations (reparent-only) → donation total preserved by construction; the 0-orphaned check confirms all landed on live survivors. cai independently re-verified Phase 1 at source (clean; agent_messages #31690).

## Residual — 15 clusters (~40 rows) sent to Gazzabyte to CONFIRM (coord #31692, Musa op#15908 "update the new operators")
Only the client knows one-vs-several:
- **11 single-token common first-names** — najihah×9, faridah×5, roslah×5, norhana×4, rohana×4, khadijah×3, humar×3, sabiah×3, latifah×2, mariah×2, subaidah×2.
- **4 genuinely-different-address** — puan liza (3 addresses), suraya mohd yussof (Compassvale vs Woodlands), puan fatimah (Bishan vs Serangoon), riduan adi (Woodlands Dr 14 — likely same, held to be safe).

On the client's per-name confirmation → merge the confirmed ones (same reversible pattern). Merge People tool remains available for client ad-hoc use (a Gazzabyte admin has already used it for 1 own merge).

## Reversibility
Every merge has a `person_merge_snapshot` row; `reverse_merge(merge_id, org, actor, payload)` restores. Script: `scratchpad/dedup_tabung_goumlyne.py` (plan/wetprove/execute modes).
