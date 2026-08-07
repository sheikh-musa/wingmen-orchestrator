# RE-REVIEW BRIEF — 089 close-gate bypass fix (money-path, CAI-RESP-412)

You are **cc-reviewer** (fresh spawn), INDEPENDENT read-only money-path RE-REVIEW. A prior reviewer returned CHANGES-REQUIRED and proved a real hole; cc-ihsanos has now shipped a fix. Your job: verify the hole is TRULY sealed and the fix introduced no new gap or regression. Be adversarial. Do NOT edit/commit/apply/merge.

Workspace: `~/wingmen/projects/ihsanos-irsyad` (separate checkout). `git fetch origin`, review `git diff origin/main...origin/feat/tabung-bankref-relocate` (now @ **e2cd415**). The fix commits are `5cb3350` (harden) + `e2cd415`.

## ⚠️ MANDATORY PRE-CHECK — DO NOT SKIP (a prior re-review was fooled by a stale tracking ref)
The repo's fetch refspec was previously narrowed to main-only, so `git fetch origin` did NOT advance `origin/feat/tabung-bankref-relocate` and a prior reviewer reviewed the OLD un-hardened tree `b651dc6` and wrongly returned CHANGES-REQUIRED. The refspec is now fixed, but VERIFY before you review:
1. `git fetch origin`
2. `git rev-parse origin/feat/tabung-bankref-relocate` — **MUST equal `e2cd41564e2286c9f92639658bc6397ce42b7f78`**.
3. `git show origin/feat/tabung-bankref-relocate:supabase/migrations/089_tabung_bankref_relocate.sql | grep -c 'endorse_close'` — **MUST be ~24, NOT ~6** (6 = the stale un-hardened tree).
4. Cross-check with the live remote: `git ls-remote origin feat/tabung-bankref-relocate` — must also be `e2cd415`.
If any check fails, STOP and report the mismatch — do NOT review a stale tree. Only proceed once you've confirmed you are reading e2cd415 with the harden present.

## The hole that was found (prior review #7354 — read `SELECT * FROM get_decision('CAI-RESP-412')` for design intent)
089 made per-tin `bank_reference` nullable (a) and relocated the audit teeth to a close-gate (c) inside SECDEF `tabung_endorse_close_report`. But terminal `closed` was reachable AROUND that fn:
- **B1**: raw UPDATE tin `banked→closed` (bank-guard allowed it with no arming; froze money cols but not status/closed_at).
- **B2**: raw UPDATE report `preparer_signed→closed` by an AUTHENTICATED org_admin via the 060 admin `FOR ALL` policy (org_id-only WITH CHECK, no status trigger) — bypassed endorse fn + close-gate + dual-control. **The real authenticated-reachable hole.**
- **B3**: reconciler listed only `status='banked'`, so a bypass-closed tin vanished from it.

## The claimed fix (verify each INDEPENDENTLY — re-run the exploits, don't trust the report)
1. **Arm-gate** — a `tabung.endorse_close` GUC that ONLY `tabung_endorse_close_report` sets, required by: the extended `tabung_tin_bank_guard` (banked→closed) AND a NEW `tabung_report_close_guard` trigger on `tabung_weekly_reports` (→closed). Froze closed_at/closed_via_report_id.
2. **RLS tightened** — the report admin `FOR ALL` policy WITH CHECK now includes `status<>'closed'`, so an authenticated client cannot PATCH a row to closed; →closed flows only via the service_role endorse fn.
3. **Reconciler extended** — `tabung_banked_awaiting_deposit` now also flags tins that reached closed via a report with no deposit_reference (adds `tin_status`).

## RE-PROVE THESE YOURSELF (ceayj pooled, rolls back — must NOT touch live goumlyne)
1. **Re-run B1**: mark a tin banked with NULL ref, then raw `UPDATE ... SET status='closed', closed_at=now()...` → MUST be REJECTED (`tabung_close_requires_endorse_fn` or equiv). Try as service_role AND via the authenticated RLS path.
2. **Re-run B2**: raw `UPDATE tabung_weekly_reports SET status='closed'...` as an authenticated org_admin → MUST be REJECTED (RLS WITH CHECK) AND as service_role without the GUC → MUST be REJECTED (trigger).
3. **THE KEY ADVERSARIAL PROBE — the GUC is client-settable.** Can an authenticated org_admin `SET LOCAL tabung.endorse_close='on'` (or equivalent) and THEN slip a raw close through? cc-ihsanos claims the RLS `WITH CHECK status<>'closed'` backstops the GUC (their cases 13/14). Verify that backstop is airtight for BOTH tins and reports, and that no OTHER policy (or service_role exposure via PostgREST) reopens the path. This is the single most important check — a client-settable GUC guarded only by a trigger would be a movable gap; confirm RLS is the real backstop for authenticated.
4. **No regression**: the sanctioned `tabung_endorse_close_report` on a legit report WITH deposit_reference+slip STILL closes successfully (arms GUC, passes both triggers, transitions tins). And legit NON-status admin edits to a report still work (the WITH CHECK tightening didn't over-block).
5. **B3**: reconciler now surfaces a bypass-closed (or any closed-without-ref) tin.
6. **Re-confirm prior PASS items still hold** post-fix: nullable relaxation (all 3 NOT-NULL sites), all 404 guards, write-once deposit_reference, close-gate logic. Confirm the fix didn't weaken any.
7. Migration idempotent; `--expect-ref` guard present; direct psycopg apply (not `supabase db push`).

## Output
Post `agent_messages` to `to_agent='cc-orchestrator'`: **APPROVE** (money-gate clear) or **CHANGES-REQUIRED** (exact defects + evidence). Advisory to cai. Operator has already given a verified expedite (window waived) — so your APPROVE is the last correctness gate before cai's §6.6 grant. Weigh accordingly: be thorough, but do not invent blockers.
