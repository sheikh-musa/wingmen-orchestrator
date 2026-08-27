# Handoff — cc-irsyad-6 (Tabung-Jumaat / Tabung-Fajar lane) — RECYCLE-FOR-HYGIENE

**Date:** 2026-08-17 ~05:24Z · **Author:** cc-irsyad-6 · **For:** orch-console (recycle) / a fresh cc-irsyad-6 instance
**Reason for recycle:** cai CAI-RESP-1035 — live context holds 7 donor PII rows (names/addresses/emails/phones) I opened before a scope correction landed, fully disclosed at the time (#24498). Recycle clears live context only; transcript is separately quarantined (CAI-1033/1034, 7-day backstop) — nothing to do about the transcript itself.
**Tag key:** `[VERIFIED]` = checked at source this turn. `[BUS]` = known only from agent_messages, not independently re-verified this turn.

## Bottom line

Two independent pieces of work landed this session on `lane/tabung-jumaat`, both fully committed and pushed. **Nothing is uncommitted; nothing lives only in this context.**

1. **CAI-903 Tabung-Jumaat combined-total** — built, wet-proven, reviewed, mig179 applied to goumlyne, PR #309 open awaiting cai's own go-live verify → merge (separate thread from the Fajar work below).
2. **Tabung-Fajar "tins still out" monitor** (CAI-RESP-1032, client op#13824/Kryptoh-Shuk) — built, wet-proven, **DORMANT** (not client-enabled), this session's main work.

## 1. CAI-903 (prior work, already reported/handed off separately)

`[VERIFIED]` `lane/tabung-jumaat @ a8a1fdd` (= current HEAD = `origin/lane/tabung-jumaat`, confirmed identical this turn). PR #309: `[VERIFIED]` state=OPEN, mergedAt=null (`gh pr view 309` this turn). mig179 (the combined-total table/RPCs) is `[BUS]` applied to goumlyne per coord (#22468/#24514) — I independently `[VERIFIED]` this myself in an earlier turn via direct psql (table + 3 action RPCs + 3 trigger fns all live, correctly `service_role`-only). Full detail already in the committed apply packet: `docs/superpowers/plans/2026-08-15-mig179-cai-apply-packet.md`. This thread does not need re-work — it's waiting on cai's own go-live verify, not on me.

## 2. Tabung-Fajar monitor — this session's build

### Why tin_type-parameterized, not fajar-specific
The direct precedent (`src/actions/tabung-keluarga.ts:1175 listUnreturnedKkTinsAction` + migration 142's `tabung_unreturned_kk_tins`/`_count` RPC pair) is fajar/kk-specific by construction (student-keyed). `tabung_umum_tins` already carries `tin_type` (fajar/kedai/masjid/masjid_ramadhan/general) as a plain column, so a `p_tin_type` parameter costs nothing extra in the RPC and makes kedai/masjid reuse it with **zero future migration** — coord explicitly asked for this generalization "if equally small" (#24536) and it was.

One structural simplification vs the KK precedent: `tabung_tin_missing_reports.tin_id` FKs `tabung_kk_tins(id)` ONLY `[VERIFIED, pg_constraint, this session]` — the missing-tin-report workflow does not extend to `tabung_umum_tins`, so there is **no anti-join** needed in the umum RPC (the KK RPC's whole reason for existing — CAI-RESP-734 — doesn't apply here). The umum RPC is a plain filtered/paginated SELECT.

### The 179-row batch (Tabung Fajar tin data, resolved — read this before re-investigating)
`[VERIFIED]` (this session, DB-side only, no client PII file reopened) — re-confirmed count=179 fresh just now. Full characterization, already relayed to cai per coord (#24555 said "no further digging needed"):

- All 179 fajar rows in `tabung_umum_tins` on goumlyne (org `73339164-7c1f-40ba-a093-33f1f292dd4c`) carry `remarks = 'IMPORT:tabung-donor-2026-08-12:fajar; orig_serial=<N>'`.
- This is the EXACT reversible-undo tag documented in `docs/clients/irsyad/tabung-donor-import-wetprove-2026-08-12.md` (in the `ihsanos-irsyad.wt-prog1` worktree) — a wet-prove doc (op#11894/CAI-834, cc-irsyad-b) that itself only documents a **PROPOSE-ONLY BEGIN..ROLLBACK stage** and says a separate "REAL reversible write" would follow once Wan confirmed 3 open decisions.
- **Conclusion: this is a real, deliberate, properly-tagged write that happened AFTER that doc — NOT a self-COMMIT-escaped-ROLLBACK bug.** I explicitly did not assert the rollback-escape theory once the tag proved otherwise; coord confirmed this was the right discipline (#24536).
- Status split (`[VERIFIED]`): of the 179, 167='counted' (historically closed money-collection events), 11='issued', 1='returned' — **ALL 179 are that one batch**; a separate, unrelated 180th fajar row (status='banked', no import tag) is the only non-batch row.
- **What this means for the discrepancy:** the 179 rows are a *historical collection-event (money) import*, not a *live tin-registration* — even the 11 "issued" ones are batch artifacts, not tins anyone issued through the app's live workflow. Net: **zero** fajar tins in this org have ever been registered "issued" through genuine live app use. The client's real ~400+ physical tins (tracked in Elly's own external file, never opened again after the one disclosed incident) were simply never individually entered into `tabung_umum_tins`. This is a genuine registration/backfill gap (case-a), not a status-vocabulary mismatch (case-b) — confirmed and reported (#24524).
- **Open paper-trail item, NOT mine to chase:** the wet-prove doc predates its own real write and there's no "confirm+write" follow-up doc. Coord is routing this to the import-owner (cc-irsyad-b / CAI-834) as a paper-trail note (#24536) — do not re-open this thread from the Fajar-monitor side.

### Current state of everything (all committed, `lane/tabung-jumaat @ a8a1fdd`)
- `[VERIFIED]` `supabase/migrations/191_tabung_unreturned_umum_tins.sql` — `tabung_unreturned_umum_tins`/`_count` RPC pair, SECURITY INVOKER, `service_role`-only EXECUTE (mirrors mig142's Design-B grant model exactly). Wet-proven on goumlyne in `BEGIN..ROLLBACK` this session: count_check=11 (exact match to the live discrepancy finding), list/pagination/search correct, wrong-tin_type param returns 0, grants confirmed `anon=f/authenticated=f/service_role=t`. **Rolled back — nothing applied.** NOT yet routed to cai §6.6 (no urgency flagged; dormant).
- `[VERIFIED]` `src/actions/tabung-umum-monitor.ts` (`listUnreturnedUmumTinsAction`) — direct sibling of `listUnreturnedKkTinsAction`, same PostgREST-degradation fallback shape for the pre-migration deploy window. **DORMANT: no route, no page, no nav entry anywhere.** Caught and fixed one real bug during TDD (BIGINT count RPC returns as a string over PostgREST; needed `Number()` coercion — same fix the KK precedent already has at `tabung-keluarga.ts:1237`).
- `[VERIFIED]` `src/actions/__tests__/tabung-umum-unreturned-monitor.test.ts` — 6/6 passing.
- `[VERIFIED]` (this session, full runs) `npm test` = 2647 passed / 7 skipped; `npm run lint:all` (13 gates) = exit 0; `npx tsc --noEmit` = exit 0, 0 errors.
- Both commits pushed: `be5a813` (mig191), `a8a1fdd` (action + test). PR #309 auto-updated (still open, gated on the separate CAI-903 verify above, not on this Fajar work).

### What a fresh cc-irsyad-6 should do next
Nothing is currently blocking or owed — this is DONE-and-dormant, not DONE-and-waiting-on-something. Concrete next steps, when/if asked:
1. **If coord wants mig191 live:** route to cai §6.6 (goumlyne-only, same residency pattern as every prior tabung migration — `tabung_umum_tins` doesn't exist on ceayj either, matching mig179's precedent).
2. **New client requirement folded in, per coord (#24654), NOT yet scoped or built:** client op#13843 wants full CRUD on Fajar tin records — serials + add/edit/remove + Elly-the-preparer visibility AND modify rights (not just read-only monitoring). This is materially bigger than the current dormant read-only view: it needs write actions (issue/edit/remove a tin record), which is a genuine build, not a small extension. **The preparer-MODIFY-rights piece specifically collides with CAI-1030 F2 (a role-tightening effort in flight elsewhere) and must be routed to cai at spec time, before building** — do not just extend the dormant monitor into a write path without that routing. Read #24654 on the bus for coord's exact framing before starting.
3. **Enablement (making the monitor visible to Elly) and the ~400-row PII backfill both stay RED** until the Irsyad-controller PII authorization doc signs (separate, pre-existing gate, unrelated to code readiness).
4. **PII discipline, binding fleet-wide now (CAI-RESP-1034/1039):** no lane opens a client-attached file, ever — not even to check columns/shape. Need shape → ask orch-console (reads headers only). Need data → work DB-side against the silo. This rule exists because of the incident in this session; do not repeat it.

## Genuinely open items (none blocking, none owed by me right now)
- mig191 not yet routed to cai (no urgency — dormant, no client exposure).
- The CRUD/write-rights extension (#24654 item 2 above) is scoped-in-principle but not designed or built.
- CAI-903's PR #309 merge is waiting on cai's own verify, tracked separately, not re-listed here in full (see the apply packet).

**Clear to recycle.**
