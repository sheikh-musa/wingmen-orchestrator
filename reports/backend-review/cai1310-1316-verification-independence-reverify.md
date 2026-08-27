# CAI-RESP-1310 + CAI-RESP-1316 — independent re-verification (lens: verification-independence)

**Auditor:** cc-quality (opus-4-8, FULL-tier). **Assigned by:** cai, inline per CAI-1001 (CAI-RESP-1316),
decision_audits rows materialised 2026-08-24 03:00:36Z, sla_hours=24. **cc-storefront conflicted-out** (its
own finding is what CAI-1310 ratifies). **Date:** 2026-08-24.

**Lens (cai's charge):** independently re-derive — WITHOUT relying on cai's summary or cc-storefront's
original report — (1) whether the live goumlyne `tabung_preparer_sign_report` genuinely contains the
CAI-1107/1297 completeness guard, (2) whether mig237-as-originally-proposed genuinely lacks it, and (3)
whether cai's own CAI-1310 claim ("I queried the live function myself and confirmed the guard") is accurate,
not merely asserted. CAI-1316 (the repair) and CAI-1310 (the ratification) share one underlying question.

---

## Method (independent, at source)

- Queried the **live goumlyne** `pg_get_functiondef` for `tabung_preparer_sign_report` myself via read-only
  `GOUMLYNE_DATABASE_URL` (single instance, oid 21432) — full 9,684-char body.
- Read **mig237-as-originally-proposed** directly from git at the pre-rework commit
  (`git show 54aef579:supabase/migrations/237_subadmin_weekly_sign_rpc.sql`), not the PR summary.
- Reconciled the apply-provenance of the reworked mig237 against the bus grant chain + the goumlyne state.

## Findings — all three questions CONFIRMED

### Q1 — Live function contains the CAI-1107/1297 guard? ✅ CONFIRMED (my own query)

The live body carries, verbatim:
- DECLAREs `v_expected_kk`, `v_got_kk`, `v_expected_umum`, `v_got_umum`.
- `-- ── Platform-wide fail-closed completeness guard [CAI-RESP-1107] ──` block.
- `IF rpt.scope IN ('keluarga','both')` → recount `tabung_kk_tins` (banked, in-period, not claimed by
  another live report) → `RAISE 'report_incomplete_tin_scope: keluarga expected % got %'`.
- `IF rpt.scope IN ('umum','both')` → recount `tabung_umum_tins` (+ `tin_type <> 'jumaat'`) →
  `RAISE 'report_incomplete_tin_scope: umum ...'`.
- Inline comment cites `[CAI-1297, mig230] Widened from exact-equality to set-membership` — the both-scope
  widening. Guard keys on `rpt.scope` BEFORE any role branch → runs **role-independently** (org_admin,
  preparer, subadmin all hit it).

### Q2 — mig237-as-originally-proposed lacks the guard? ✅ CONFIRMED (my own git read)

`git show 54aef579` (original, pre-rework):
- **Zero** guard tokens (`grep -c` = 0 for `v_expected_kk` / `report_incomplete_tin_scope` /
  `CAI-RESP-1107` / `scope IN ('keluarga'`).
- Its **own header** states (lines 5, 12): *"REPLACE of tabung_preparer_sign_report **(mig133)**"* and
  *"THREE changes vs the live **mig133 body** (diffed to confirm nothing else…)"* — the builder diffed
  against **stale mig133**, not the live composed body (mig133+197+230).
- It is a full `CREATE OR REPLACE FUNCTION` (line 57). Applying it would have **overwritten the live
  guarded body with a guardless one for every caller** — the exact CAI-975/mig189 stale-baseline class.
  Invisible to a positive-path wet-prove: a removed guard only shows on an **incomplete-snapshot** sign.

### Q3 — cai's CAI-1310 "I queried live and confirmed the guard" claim accurate? ✅ CONFIRMED

My independent query corroborates cai's CAI-1310 description **exactly** (the 4 DECLAREs + the
`scope IN (keluarga,both)` recount-and-RAISE, landed via mig197/CAI-1107, widened by mig230/CAI-1297, and
mig237-proposed lacking all of it). cai's ratification was a genuine at-source re-derivation, **not a
rubber-stamp**. The mig238 split (independent function, no shared invariant) is also sound.

### CAI-1316 (the repair) — ✅ SOUND

CAI-1316 owns the CAI-1001 miss (FULL tier, no inline auditor — the mechanism CAI-1293→1301 caught its own
author's miss, #32751) and repairs it per CAI-1001's own rule: names cc-quality inline, lens
verification-independence, conflicts-out cc-storefront. Since the underlying ratification (CAI-1310) is
confirmed accurate here, the repair-correctness — the same underlying question — holds.

---

## Reconciliations verified along the way (surfaced, both already CLOSED)

1. **Apply-provenance of the reworked mig237 — GATED, clean.** The live function now also carries the 3
   Piece-2 subadmin deltas + the preserved guard, i.e. the *corrected* mig237 is applied to goumlyne. I
   confirmed this was **properly gated**, not an ungated money-path apply: adversarial RE-verify PASS
   (cc-storefront #32669, body-diff vs live — guard byte-identical), independent re-wet-prove PASS 10/10
   incl. the required new negative for BOTH callers (orch-console #32672), **cai §6.6 grant CAI-RESP-1313**,
   applied with an apply-time body-diff-vs-live re-check (#32678). The live state I queried IS the
   correctly-reworked, granted, applied body. This reinforces — does not weaken — the CAI-1310 outcome.
2. **git≠DB drift (#483 → piece1 topic branch, not main) — CLOSED.** PR#483 merged into
   `feat/subadmin-weekly-reports-piece1`, not main, so mig237/238 were applied+granted on the DB but absent
   from main's git tree (fail-safe: app blocked subadmin pre-RPC → over-restrictive, not a security hole;
   caught #32732–36, own-missed by orch-console). Re-checked `origin/main` now: **mig237 + mig238 files are
   present** and `isAdminEquivalent(role)` is wired in the weekly-report actions — the catchup landed,
   git==DB coherence restored.

Note (migration ledger): goumlyne `supabase_migrations.schema_migrations` records tabung functions only up
to 133/134; migs 197/230/235/237/238 are applied via direct-psycopg out-of-band and are NOT in the ledger.
The ledger is therefore not a source of truth for these function applies — provenance was reconciled via
the live `pg_get_functiondef` + the bus grant chain instead. (Coherence observation, not a CAI-1310 finding.)

---

## VERDICT

**CAI-RESP-1310 ratification: RE-VERIFIED AT SOURCE — ACCURATE (CONFIRMED).**
**CAI-RESP-1316 repair: SOUND (CONFIRMED).** (opus-4-8, FULL, lens verification-independence.)

Independently re-derived from the live goumlyne function and the original migration git blob (not cai's or
cc-storefront's summaries): the live function genuinely carries the CAI-1107/1297 completeness guard;
mig237-as-originally-proposed genuinely lacked it (built CREATE OR REPLACE against stale mig133); and cai's
own at-source verification claim holds up exactly. The HOLD-then-re-derive was the correct call and the
regression was caught before it reached production. Both reconciliations (gated apply of the reworked
mig237; git==DB drift closure) verified closed. No defect in the ratification or the repair.

**Refs:** CAI-RESP-1310, CAI-RESP-1316, CAI-1290 (adversarial-verify program), CAI-RESP-1107 (guard origin,
mig197), CAI-RESP-1297 (both-scope widening, mig230), CAI-RESP-1313 (mig237 grant), CAI-RESP-1311 (mig238
grant), CAI-1001 (inline-auditor rule), CAI-1293→1301 (the catching mechanism). Bus #32635–#32751.
Verdict routes to cai; decision_audits rows for CAI-RESP-1310 + CAI-RESP-1316 completed.
