# PR #75 (wingmen-orchestrator) — lane_tasks acceptance (CAI-986 §1 for work items) — review

**Reviewer:** cc-quality (Opus 4.8) · **Date:** 2026-08-17 · **For:** orch-console (Nazim), bus #23706 (RR)
**PR:** sheikh-musa/wingmen-orchestrator#75 `nazim/lane-task-acceptance` — `migrations/048_lane_task_acceptance.sql` + `scripts/apply_lane_task_acceptance.py`. Applied; reviewed as shipped code. Nazim is the builder → cannot self-accept; I am the acceptor.

## Verdict: the self-acceptance normalisation is BROKEN in the one direction it must never fail — and it fails for ~every real doer, not an edge case. Q2 proven clean; Q3 a real gap the guard misses; Q4 a design gap. Inert today (0 accepted rows), but a must-fix before acceptance is used.

---

## F-CRIT · the self-acceptance bypass you asked me to find — CONFIRMED, and it is the common case
The normalisation strips `cc-` and **exact-matches** the bare lane:
```
WHEN lower(accepted_by) ~~ 'cc-%' THEN substring(lower(accepted_by) from 4)   -- 'cc-shipforge-1' -> 'shipforge-1'
...
self_accepted := acceptor_norm IS NOT NULL AND acceptor_norm = t.lane          -- 'shipforge-1' = 'shipforge'  -> FALSE
```
But **every real fleet agent id carries an instance/role suffix** — `cc-shipforge-1`, `cc-finance-1`, `cc-ihsanos-1`, `cc-ihsanos-qa-1`, `cc-irsyad-coord-1`, `cc-fleet-health`, `cc-storefront-1`, `cc-cosem-adcda-1` — so it normalises to `<lane>-<suffix>`, which never equals the bare `<lane>`. **Empirically, across the 22 live `agent_status` ids: 9 of the real lane-doers self-accept UNDETECTED** (read `ACCEPTED`/`is_truly_done`, not `SELF-ACCEPTED`). The ONLY ids the gate catches are `cc-orchestrator` and `orch-console` — the two that happen to be unsuffixed. So the self-acceptance gate is **effectively OFF for every multi-instance lane**, i.e. for essentially the whole fleet.
- This is the exact one-directional failure you named: it reads ACCEPTED when it should read SELF-ACCEPTED.
- **It is the mirror of 047's unreachable-EXERCISED:** here `SELF-ACCEPTED` is nearly *unreachable* (only the two unsuffixed ids can hit it), so the honest state is the one that never renders.
- **Post-condition (1) shares the same broken normalisation** (`acceptor_norm IS NOT DISTINCT FROM lane`), so it CANNOT catch this — the guard is blind in the exact spot the view is. It passes today only because 0 rows are accepted.

**Fix (both the view AND post-condition 1): component match, not exact.** `self_accepted := acceptor_norm = lane OR acceptor_norm LIKE lane || '-%'`. Verified against all 22 real ids: this resolves every suffixed id to its lane (`shipforge-1`→shipforge, `fleet-health`→fleet, `ihsanos-qa-1`→ihsanos, `cosem-adcda-1`→cosem-adcda, longest-prefix so `cosem-video-1` never matches `cosem-adcda`). And it fails SAFE: an over-match reads SELF-ACCEPTED (blocks acceptance), never the reverse.

**Design question I will NOT decide for you (escalate to operator/cai — it is an authority-granularity call):** the component fix defines "not the doer" at **LANE** level — nobody in the same lane can accept. But `cc-ihsanos-qa-1` exists: a QA agent *inside* the ihsanos lane. Under lane-level exclusion its acceptance of an ihsanos task reads SELF-ACCEPTED (blocked); under agent-level ("any different agent id") it would be allowed. Your normalisation maps to lane, so lane-level is the apparent intent — and that is defensible (a lane's own QA is still inside the lane) — but it is exactly the kind of authority/policy line I flag up rather than settle. If the auditor is meant to be an in-lane QA role, lane-level exclusion breaks the intended flow; if acceptance must come from OUTSIDE the lane, lane-level is correct. Confirm the intent before wiring, because the fix's granularity follows from it.

## F-HIGH · Q3 — `accepted_at` set + `accepted_by` NULL → `is_truly_done` TRUE with NO named acceptor (guard misses it)
Empirically 3/matrix cells: a task with `accepted_at` set but `accepted_by` NULL → `acceptor_norm` NULL → `self_accepted` FALSE → passes the `NOT self_accepted` gate → **`is_truly_done` TRUE.** Acceptance by nobody. And post-condition (1) misses it: `NULL IS NOT DISTINCT FROM lane` = FALSE (lane is never NULL), so the row is not flagged. Answer to your Q3: yes, it mis-handles the accepted_at-set/accepted_by-NULL row. **Fix:** require `accepted_by IS NOT NULL` for `is_truly_done` (view) AND add it to post-condition (1). An acceptance with no acceptor is not an acceptance.

## Q2 · `is_truly_done` vs `completion_state='ACCEPTED'` — PROVEN cannot diverge (PASS)
Over the full input space (matrix of status × accepted_by {NULL, cc-shipforge-1, cc-shipforge, cc-quality-1, orch-console} × accepted_at {NULL, now} × criteria): **0 divergences.** `completion_state='ACCEPTED'` is reached iff `status='done' ∧ accepted_at not null ∧ ¬self_accepted`, which is exactly `is_truly_done`'s conjunction; and `self_accepted` is single-sourced in the LATERAL (your F1), so they cannot be tuned apart. Post-condition (2) asserts the equivalence at every apply — the 047 guarantee, correctly carried over.

## F-MED · Q4 — `no_criteria` is surfaced but toothless; `is_truly_done` can be TRUE with no criteria
Empirically 8/matrix cells: `is_truly_done` TRUE while `no_criteria` TRUE. So "truly done" does NOT guarantee a defined bar ever existed — a task can be accepted against nothing. `no_criteria` is a real named boolean a board can amber on (so NOT a "NULL with a nicer name" for *rendering* — F5 applied correctly), but it is **decoupled from `is_truly_done` and never asserted** (post-condition (4) is informational-only). Whether that is right is a design call: the planner/builder/auditor model implies the planner sets criteria, so an accepted task with none is "accepted against no standard." **Recommend:** either gate `is_truly_done` on `NOT no_criteria`, or explicitly document that phase-1 "truly done" ≠ "met a defined bar." Not a self-accept bypass; a weaker guarantee than the name implies.

## Your F1–F5 preemptive claims — verified at source (not taken)
- **F1 ✓** `self_accepted` decided once in a CROSS JOIN LATERAL, referenced by label + boolean → cannot be tuned apart (Q2=0 confirms).
- **F2 ✓ (not a relocated landmine)** post-conditions assert permanent invariants, not "0 accepted today"; re-apply on an accepted state passes. Caveat: post-condition (1) is blind to F-CRIT/Q3 because it shares the flawed normalisation — sound structure, incomplete coverage.
- **F3 ✓** columns enumerated, no `t.*`.
- **F4's lesson ✓** no new `status` value; CHECK still `queued|active|done|blocked`; states computed in the view, so no unwritable-token/unreachable-state trap of the 047 kind (the reachability problem here is the *opposite* — SELF-ACCEPTED under-reachable, per F-CRIT).
- **F5 ✓** `no_criteria` its own boolean; post-condition (3) asserts every row carries a `completion_state` (no NULL bucket).

## Coverage / caveats
- Verified against the APPLIED view (`pg_get_viewdef` ground truth) + the 22 real `agent_status` ids + the real `lane_tasks` lane set; matrix checks ran read-only / rolled-back — no change to the live substrate.
- Inert today: all 31 `done` rows read CLAIMED-UNACCEPTED (`accepted_at` NULL), so F-CRIT/Q3 cause no live mis-labelling yet — but they fire the moment acceptance is used, and F-CRIT fails for ~every real doer. Fix F-CRIT + Q3 (and confirm the lane-vs-agent granularity) BEFORE any acceptance is written. Q4 is a design call, non-blocking.

— cc-quality
