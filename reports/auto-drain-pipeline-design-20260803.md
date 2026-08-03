# Auto-drain pipeline — design (op-approved 2026-08-03)

**Owner:** Nazim / orch-console. **Operator ask:** "you tell me → I file it to the owning lane → the lane drains it → it clears. My plate stays clean." Approved ("yes please" + "proceed", op#9801/9805).

## Problem
The operator's `operator_backlog` ("Your asks") piled up with delegated build work. Moving items to `lane_tasks` (done) declutters his plate, but lanes don't currently **drain** their queue — they act off their bus inbox + judgment, and `lane_tasks` was a display, not a worklist they poll. So work can sit unworked and invisible.

## Two halves
- **Part 1 — VISIBILITY (✅ LIVE, fc-v24).** Console "Lane worklists" section renders `lane_tasks` per lane (queued / working / blocked). The operator can now SEE each lane's worklist + his blocked money-gates (amber "⏳ awaiting you").
- **Part 2 — DRAIN (this design).** Make queued tasks actually get worked + clear.

## Part 2 mechanism (proposed)
1. **File** (Nazim, the router — human checkpoint): an ask → a `lane_tasks` row under the owning lane. Money/cai/residency asks are filed `status='blocked'` (never auto-drain — see policy).
2. **Seed + nudge**: on filing a `queued` task, seed the owning lane's bus inbox (`agent_messages` to that lane, attributable) + fire a verified `lane_nudge` so the lane actually reads it (closes the [[reference_lane_stalls_on_unread_go_bus_not_telegram]] stall-class). 
3. **Drain** (the lane): lane doctrine gains one line — "your `lane_tasks` queue (status queued, oldest-first) IS your worklist; work it, and on completion call `scripts/lane_task_done.sh <id>` to flip it `done`." Done tasks already drop from the board (query excludes done).
4. **Auto-nudge daemon** (prevents pile-up): a small launchd job watches for `queued` tasks whose owning lane is idle + hasn't been nudged recently, and re-nudges — mirrors `fleet_stall_watch.py`. Never touches `blocked` rows.
5. **Clear**: done → off the board automatically. The operator watches queued → working → done live (Part 1).

## ✅ cai RATIFIED — CAI-RESP-700 (2026-08-03, 24h challenge window on POLICY only; build now)
4 gated classes unchanged (none moved out). 6 BINDING fail-closed conditions — the design as first written failed OPEN (keyword classifier), so these are load-bearing:
1. **FAIL CLOSED, not keyword-only.** Auto-drain requires POSITIVE confidence the task is non-gated. Router-unsure/ambiguous → filed `blocked`. Default verdict is BLOCKED; drainable is the EARNED state, never the fallback.
2. **Gate at COMPLETION too.** `lane_task_done.sh` must REFUSE + re-block a done-flip whose work asserts any gated class (esp. a systematic-fix claim, or money/prod-DB/residency that slipped a non-gated filing). Both ends of the pipe.
3. **Done ≠ assertion.** A non-gated done RECORDS self-verify evidence in the row before it drops off the board — a false done must not silently vanish.
4. **Clear authority is GOVERNANCE (SoD, CAI-339).** I FILE + CLASSIFY (ops); I do NOT clear a gated block. Only cai or a verified `operator_message` clears money/irreversible/residency/systematic-fix. A lane NEVER clears its own block.
5. **Seed on the bus, nudge lifecycle-only (CAI-253).** Task content → `agent_messages` (attributable). `lane_nudge` carries ZERO task content.
6. **Migrations NEVER auto-drain.** Schema/prod-DB tasks stay blocked → the named-file grant (cai-only). Auto-drain never touches a migration.

**FIRST-RUN CONTROL (binding, CAI-564):** verify with TWO tasks — a non-gated one that DRAINS clean AND a deliberately-gated one that STAYS BLOCKED and can't be lane-cleared. PREDICT both outcomes before running. Proving the gated task will NOT drain is the half that matters. Report the control result to cai before any fleet-wide enable.

## GATE-SAFETY POLICY — the crux (RATIFIED above as CAI-RESP-700)
Auto-draining unattended work is safe ONLY if the money/irreversible/residency boundary is enforced by the mechanism, not by hope. Proposed policy:
- **NEVER auto-drain** (stay `blocked` until an explicit gate clears): anything touching a **money path** (charging, payouts, billing rails going live), **irreversible prod-DB** mutations, **client-data residency**, or a **"systematic bug fixed" claim**. These are exactly the 4 gated classes from the #45 support-agent spec.
- **Filed blocked by default** if the task title/detail trips any gate keyword OR the router marks it gated. A blocked task shows "⏳ awaiting you/cai" and requires an explicit operator/cai clear to move to `queued`.
- **Auto-drain allowed**: non-gated build/UX/infra work (the majority) — a lane picks it up, works it, self-verifies, marks done.
- **The router (Nazim) stays in the loop** on filing/classification — the DRAIN is automated, the ROUTING keeps a light human checkpoint so nothing fires wrong.

**cai question:** ratify this gate-safety boundary (which task-classes may auto-drain vs are hard-blocked), and confirm the filed-blocked-by-default + explicit-clear-to-queue rule. Once ratified, I build the mechanism (seed+nudge, `lane_task_done.sh`, auto-nudge daemon, the one lane-doctrine line).

## Build order (cai ratified — build now, under conditions 1-6)
1. **Classifier (condition 1, fail-closed):** `file_lane_task` helper that files `blocked` UNLESS positive-confidence non-gated. Gate signals: money/charge/payout/billing-live, prod-DB/migration/irreversible, residency/PII/client-data, "systematic … fixed" claims. Ambiguous → blocked.
2. **`lane_task_done.sh` (conditions 2+3):** a lane flips its OWN task done ONLY if (a) the work asserts no gated class (else refuse + re-block, loud) and (b) it records self-verify evidence in the row. Never clears a `blocked` row.
3. **Clear helper (condition 4, SoD):** `clear_lane_gate.sh` — cai or a verified `operator_message` ONLY moves a `blocked` gated task → `queued`. A lane cannot call it for itself.
4. **Seed+nudge on file (condition 5):** filing a `queued` task seeds the lane's `agent_messages` inbox (content) + fires `lane_nudge` (lifecycle signal, zero content).
5. **Auto-nudge daemon:** mirror `fleet_stall_watch.py` — re-nudge a lane with `queued` (never `blocked`) work sitting idle. `blocked`-safe by construction.
6. **One lane-doctrine line:** "your `lane_tasks` queue (status=queued, oldest-first) IS your worklist; work it, self-verify, mark done via `lane_task_done.sh`."
7. **TWO-TASK CONTROL (binding):** predict + prove — non-gated drains clean, gated stays blocked + un-lane-clearable. Report to cai (CAI-RESP-700) before fleet-wide enable.
