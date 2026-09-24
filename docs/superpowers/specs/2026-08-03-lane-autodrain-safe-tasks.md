# Spec: lane auto-drain of SAFE tasks (deny-by-default dispatch)

> **SUPERSEDED (2026-08-03) — folded into the converged spec of record.** The operator told BOTH hub and orch-console (Nazim) to build auto-drain (op#9846); cai bound them to ONE boundary (CAI-RESP-703 cond 7: one classifier of record). Convergence: **Nazim owns design + the single classifier + console** (his cai-ratified CAI-700/701 spec, `reports/auto-drain-pipeline-design-20260803.md`, to be committed shared-readable); **hub owns lane-side execution** (queue seed+nudge, lane-doctrine, auto-nudge). The safety model + deny-by-default + hard-coded keyword floor + DB-role-barred lane writes + per-flip artifact below remain the ratified requirements (CAI-703 conditions 1-7) — kept here as hub's contributed record; the CLASSIFIER lives in Nazim's spec, not a second one here. Do NOT build a parallel dispatch column/classifier from this file.



**Requested by:** operator op#9838→#9842 ("is it auto draining or are you manually giving it tasks" → "yes please" to auto-drain the lighter, non-gated items).
**Author:** cc-orchestrator (hub). **Status:** DRAFT — safety model first; implementation gated behind cai doctrine-fit review.
**Date:** 2026-08-03.

## Problem
Today the hub hand-delegates every lane task via attributable bus rows (`agent_messages`) and sequences the whole backlog itself. cc-irsyad idles on Telegram between directives (a hub bus GO can sit unread until Nazim cross-host-nudges). For LIGHT, non-gated items (UI tweaks, wording, doc/board updates) this hub-in-the-loop step is pure latency.

## Goal
Let a lane SELF-CLAIM and drain tasks that the hub has explicitly pre-classified as SAFE, while EVERY money/PII/access/residency-touching task stays hub-directed and gated. Reduce latency on trivia without weakening a single gate.

## Hard safety invariants (non-negotiable — this is the whole point)
1. **Deny-by-default.** A task is auto-drainable ONLY if explicitly marked `dispatch='auto'`. Default is `'hub'`. Un-classified ⇒ hub-directed.
2. **The hub is the SOLE writer of `dispatch='auto'`.** A lane can READ its auto queue and CLAIM from it; it can NEVER promote a task to `auto`. (Enforce by convention now; by a DB grant/policy if lanes ever get scoped roles.)
3. **Gated classes are NEVER `auto`.** Anything touching money, PII, access/permissions, residency, irreversible ops, or client-facing 'done' messaging is `dispatch='hub'`, always. The hub classifier refuses `auto` for these (checklist below). This preserves the fleet gating doctrine (money/PII/access/residency = hub+cai gate) intact — auto-drain only ever moves pre-authorized trivia.
4. **Atomic claim, no double-work.** Claim via `FOR UPDATE SKIP LOCKED` so two pollers can't grab the same row.
5. **Attributable + auditable.** A claimed task records who claimed it and when; the lane reports completion on the bus like any directive. No silent work.

## Schema (migration, direct psycopg — NEVER supabase db push)
`ALTER TABLE lane_tasks ADD COLUMN dispatch text NOT NULL DEFAULT 'hub' CHECK (dispatch IN ('hub','auto'));`
(Optionally `claimed_by text`, `claimed_at timestamptz` if `started_at`+a lane marker isn't enough.)
Deny-by-default falls out of the `DEFAULT 'hub'`.

## Hub classifier — checklist to set `dispatch='auto'` (ALL must be true)
- [ ] No money path (no donation/tabung/fee/keyword-routing/receipt-numbering write or read that feeds a money decision).
- [ ] No PII / documents / personal data.
- [ ] No access/permission/grant/RLS/role change.
- [ ] No residency / silo / cross-tenant surface.
- [ ] Not irreversible (no delete/migration/deploy/config-set).
- [ ] Not a client-facing 'done'/commitment message (those stay hub-reviewed per the #45 pilot).
- [ ] Reversible + low-blast-radius if done wrong (UI copy, board/doc update, internal refactor with tests).
Any box unchecked ⇒ stays `dispatch='hub'`. When in doubt, `hub`.

## Atomic claim protocol (lane side)
```sql
UPDATE lane_tasks SET status='active', started_at=now()
WHERE id = (
  SELECT id FROM lane_tasks
  WHERE lane=%s AND status='queued' AND dispatch='auto'
  ORDER BY priority_rank NULLS LAST, id
  FOR UPDATE SKIP LOCKED LIMIT 1
)
RETURNING id, title, detail;
```
Work it → `status='done'`, report completion on the bus → poll again → when the query returns 0 rows, idle.

## Lane idle-drain loop (cc-irsyad)
Precondition: no unread hub directive in its bus inbox (hub-directed work always wins). When idle:
1. Claim one `auto` task (above). If none → idle/return to waiting.
2. Do it (it's pre-authorized safe; still normal build discipline: tests, no self-deploy — but no gate needed since the class is gate-free by construction).
3. Mark done + bus a one-line completion. Loop.

## Doctrine fit / cai review points
- Touches ORCH-TOPOLOGY-001 pen "hub delegates via attributable bus rows": lanes now self-claim PRE-AUTHORIZED work. Compatible because deny-by-default + hub-sole-classifier + gated-never-auto. **Route to cai for ratification before enabling** — it's a change to how work is authorized, even if it can't touch a gate.
- Anticipated by doctrine ("interim until the autoscaler subsumes it") — this is a bounded, safe step toward that.

## Implementation increments + owners
1. **cai heads-up + ratify** the dispatch model (hub). ← do first, non-blocking on trivia.
2. **Migration** add `dispatch` column (hub, guarded psycopg).
3. **Hub classifier**: when creating/triaging a lane_task, run the checklist; set `auto` only for gate-free trivia. Backfill-classify current `irsyad` queued tasks (most are money/PII → stay `hub`; only UX/docs → `auto`).
4. **Lane idle-drain loop** in cc-irsyad boot/idle behavior (cc-irsyad + Nazim, Mini-side) — the claim query + completion report.
5. **Observe**: log every auto-claim; if any mis-classification surfaces, tighten the checklist.

## Honest scope note (told to operator, #9843)
Most of the CURRENT irsyad backlog (manual tabung, missing-TIN docs, donation reassign) is money/PII → stays `hub`. Auto-drain's near-term surface is the light UX/wording/doc items. Value is real but bounded; the safety model is the point.
