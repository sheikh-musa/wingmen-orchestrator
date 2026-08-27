# scripts/compact_handoff.py — cc-quality recovery-critical review

**VERDICT: CONDITIONAL PASS.** Safe to `--apply` on **keyword-headed** handoffs (cc-quality / nazim / irsyad-6 — current state provably kept, verified on real data). **MUST NOT `--apply` on coord's handoff (or any 0-keyword-header handoff)** until the section[0] fix below — coord's identity/current-state would be collapsed. File-wrapper safety guards all verified. Two findings below.
**Reviewer:** cc-quality (opus-4-8) · **Date:** 2026-08-23 · **Dispatch:** orch-console #31805. Not money-path, but recovery-critical — reviewed with equal rigor (read code, ran suite, ran on REAL handoffs + adversarial cases).

## What's SOUND (verified, not asserted)
- **Pure core**, no I/O/clock/globals; 8/8 provided tests pass.
- **Under-cap → returned UNCHANGED**; no `## ` structure → UNCHANGED (never blind-truncates). ✅
- **Current state kept on all THREE keyword-headed real handoffs** (dry-run): cc-quality (`⚑ FINAL STATE`, under-cap→unchanged), irsyad-6 (`…READ FIRST`, 104KB→96KB, block kept), nazim (`FINAL STATE`, 116KB→75KB, block kept). ✅
- **Cap yields to safety** — irsyad-6 (96KB) and nazim (75KB) stay ABOVE the 60KB cap rather than truncate kept current-state. Correct (documented). Operator note: for a large-current-state handoff the result may exceed cap.
- **Idempotent in ALL cases** including the cap-yielded outputs (irsyad-6 96726B and nazim 75325B each: once==twice). ✅
- **File wrapper guards all fire** (tested): DRY-RUN by default (writes nothing, file untouched); `--apply` writes `<path>.<stamp>.bak` = the ORIGINAL *before* overwriting, then shrinks the file; re-apply is a no-op (idempotent, no new .bak); under-cap is a no-op. Backup-before-write ordering is correct (original recoverable if the second write fails). The `refuse empty/larger` guard is defensively correct (fail-closed) though unreachable for valid compact_handoff output (it only ever removes content).

## ⚠ FINDING 1 — MEDIUM (recovery data-loss): non-keyword current-state block is COLLAPSED
The "keep current state" heuristic = keyword-header (`FINAL STATE|SUPERSEDES|READ FIRST|LATEST DELTAS`) OR last `keep_recent` sections. A current-state/identity block at the **TOP or MIDDLE under a header WITHOUT one of those keywords**, in a handoff that exceeds cap with > keep_recent sections, has its **BODY collapsed to a pointer (lost)** — only the header text survives. **Demonstrated:**
- A top block `## ⚑ CURRENT STATUS — where things stand NOW` (no keyword) + 50 deltas → header survives as a pointer, **body (3000B) LOST**.
- Coord-shape (`## 0. IDENTITY`, `## 1. CURRENT WORK`, then 48 deltas) → **IDENTITY body LOST, CURRENT-WORK body LOST** (neither keyword-flagged nor in last-8).

**Why it matters:** coord's real handoff has **0 keyword-headed sections** (numbered `## 0. IDENTITY…` convention) and is the **697KB case this tool was built to compact**. Today it's 14KB (under cap → unchanged, safe), but the moment it regrows past cap, `--apply` would drop its identity + any non-bottom current-state → a reset coord boots from a handoff missing its authoritative state. The three keyword-headed handoffs are safe *because* they carry a keyword; coord does not.

**FIX (cheap, closes the gap for both conventions): ALWAYS keep section[0] verbatim.** In the nazim convention section[0] is the FINAL STATE block (already kept); in the coord convention section[0] is the identity/standing-role block. Keeping the first section costs one section and guarantees the top-of-file authoritative block is never collapsed. (Alternative: require coord to add a `READ FIRST`/`FINAL STATE` keyword to its live block before compaction — more fragile, per-author.) **Do not `--apply` on coord's (or any 0-keyword) handoff until this lands.**

## FINDING 2 — LOW (under-compaction + stale-kept): ALL keyword blocks are kept, including superseded ones
`always_keep` keeps EVERY keyword-matching section. nazim's handoff has **9 `FINAL STATE` sections** (one literally `## ⚑⚑⚑ (SUPERSEDED) FINAL STATE`) — the tool keeps all 9 verbatim, which is why nazim only shrinks 116KB→75KB (still unreadable-whole). This is SAFE (the current block is kept, order preserved so the reader can pick the top/newest) — but it (a) under-compacts the append-a-new-FINAL-STATE-each-cycle convention (the tool barely helps), and (b) keeps blocks explicitly marked SUPERSEDED. Not a corruption; an efficiency + mild "keeps stale as if current" edge. Consider (convention-dependent, optional): collapse older keyword blocks to pointers, keeping only the newest — but this is risky to automate (which end is newest depends on convention), so keeping-all is the defensible default; flag for a design decision, not a required fix.

## Test-suite gaps (recommend adding — I verified these manually)
The suite has NO test for: (1) the file wrapper at all (`.bak` write, refuse-larger/empty, dry-run default, no-op-on-re-apply); (2) a non-keyword top/middle current-state block (Finding 1); (3) idempotence on a cap-yielded (> cap) output. All three pass/behave-as-found in my manual runs, but a recovery-critical tool should lock them in tests.

**Bottom line: CONDITIONAL PASS. Pure/idempotent/cap-yields-to-safety, wrapper guards fire, and current state is provably kept for keyword-headed handoffs (mine/nazim/irsyad-6) — those are cleared to `--apply`. BLOCK `--apply` on coord's non-keyword handoff until section[0] is always-kept (Finding 1). Finding 2 (multi-keyword under-compaction) is LOW/optional. Add the three missing tests.**

---
## RE-CONFIRM (SRE fix landed, 2026-08-23) — PASS, cleared for first --apply
cc-fleet-health closed Findings 1+2 + all 3 test gaps (#31817). Re-reviewed with the same rigor (ran tests + all 4 real handoffs + adversarial):
- **FINDING 1 FIXED:** `keep = {0} | last-keep_recent` — section[0] ALWAYS kept verbatim (keyword-only heuristic dropped, covers both conventions). Real-data: coord 697KB→12KB keeps `## 0. OPERATOR-CRITICAL DIRECTIVES` body verbatim; all 4 real handoffs have current-state AT section[0] and it survives. `test_nonkeyword_section0_identity_kept_verbatim` locks it.
- **FINDING 2 FIXED:** older FINAL-STATE blocks collapse (only newest=section[0] kept) — nazim 116KB→45.7KB (under cap; was 75KB/over). `test_older_final_state_blocks_collapse_and_get_under_cap` locks it.
- **3 TEST GAPS CLOSED:** file wrapper (dry-run-default / apply-writes-.bak==original / refuses-larger via monkeypatch / no-op-under-cap), non-keyword section[0], cap-yielded idempotence. **15 tests green** (re-run). All 4 real handoffs idempotent.
- **LOW/advisory (non-blocking):** the fix trades keyword-anywhere-keep for section[0]-only-keep → it now assumes current-state == section[0]. True for both fleet conventions on all real handoffs + documented — but a handoff placing FINAL STATE at section[1+] (violating both conventions) would lose it. The section[0]=current-state convention is now the contract; keep live-state at section[0].
**RE-CONFIRM VERDICT: PASS.** Cleared for the first `--apply` (Nazim's own handoff, .bak + eyeball, then coord).
