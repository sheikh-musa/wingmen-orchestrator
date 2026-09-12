# irsyad-autoscaler — design v1 (first cut for Nazim's gate)

**Owner:** cc-orchestrator (hub, fleet-topology domain). **Status:** DESIGN — not built, not armed. **Gate:** orch-console (Nazim) reviews this at source before any build; ships INERT (detect+log only) → wet-prove → his gate → arm. **Origin:** Musa full-tilt GO + full autonomy (via Nazim 39331); demand-scaled pool course-correction (Musa op19217/19259 "supervised first").

## 1. Purpose & scope
An elastic pool of **generic irsyad WORKER lanes** on **gzb**, each on the **musa2** OAuth pool, spun on demand and wound down on idle — so irsyad work scales without 12 static lanes, and no idle lane burns tokens.

**In scope:** ephemeral irsyad worker lanes (the `cc-irsyad-*` family doing tabung/student/bankimport/receipt-class work).
**OUT of scope (never auto-killed / never autoscaler-owned):** `cc-irsyad-coord` (standing supervised coordinator, owns the gazzabyte-irsyad poller), any client-poller lane, any money-path lane mid-operation, and all singletons (hub/SRE/cai/console). These are PROTECTED (§5).

## 2. Demand signal (what triggers spin-up)
**Principle:** spin only for *real, unclaimed* irsyad work; measure a queue depth, not a rate.

**v1 signal (proposed — needs Nazim to confirm the canonical queue source):** depth of **unclaimed irsyad work items** = the count of actionable rows addressed to the irsyad worker family that no live worker has claimed, past a short grace. Candidate sources, in preference order:
1. A dedicated dispatch signal from **coord** (coord is the supervised coordinator; it already triages gazzabyte work → it is the natural producer of "N tasks need a worker"). Cleanest: coord writes/updates a claimable queue; the autoscaler reads depth.
2. Failing that: `agent_messages` to `cc-irsyad*` that are `requires_response` + unresponded past grace AND not owned by a live lane.
3. NOT a signal: bus chatter, heartbeats, or operator DMs.

**Spin rule:** `depth ≥ SPIN_THRESHOLD` (v1: 1) AND `live_pool_lanes < MAX_LANES` → spin +1. **Supervised-first (Musa op19259):** in v1 the autoscaler DETECTS + PROPOSES a spin-up (bus row to console/hub) and waits for a confirm before spinning; fully-auto spin is a later, separately-gated arming step.

**Open Q for Nazim:** which queue source is canonical (1 vs 2)? This determines the "claim" primitive in §4.

## 3. Placement
- **Host:** gzb (spawn via `lanes.sh` / `launch_lane_as.sh` executed on gzb). Set **gzb as the irsyad spawn-host default** so even a manual spin lands correctly.
- **Token:** musa2 — automatic via the proven session-name → `.group_default_token.irsyad` → `musa2-oauth-token` pointer (no per-lane token handling).
- **Identity:** DISTINCT per lane (avoid the `cc-irsyad-N`-collision drift the roadmap warns of) — allocate a distinct sub-tag via the existing bounded `pg_try_advisory_xact_lock` + smallest-free-N scan (auto_agent_id), base from `fleet_lanes.base_agent_id` via `CC_BASE_OVERRIDE` (the coord-proven pattern).
- **Env hygiene:** each spawn inherits the CAI-1225 defense (no write DSN in the lane env; tmux-server-env stays scrubbed) — fold the boot-time server-env scrub in so a long-lived gzb tmux server can never re-leak write DSNs to a pool lane.

## 4. Lifecycle state machine (over `fleet_lanes.desired_state` + `lanes.sh`)
States per lane row: `down → claimed(spinning) → up(working) → draining → down`.
- **Atomic check-and-claim** on the `fleet_lanes` row (CAI-RESP-422 pattern) before any spin/kill — a conditional UPDATE that flips state only if it reads the expected prior state, so two autoscaler ticks (or a tick racing a manual op) can never double-act one lane.
- `desired_state` is the source of truth; `lanes.sh` is the actuator; the autoscaler only ever moves a lane through legal transitions it has claimed.

## 5. Wind-down + HARD INTERLOCKS (Nazim's bar — fail-safe by construction)
- **Never kill mid-work — idle-proof BEFORE wind-down:** a lane is wind-down-eligible only if ALL hold: no claimed/active work item, no bus activity within an idle-grace, no active job, agent_status not mid-turn. Reuse `scripts/lib/lane_winddown.py` as the actuator; the idle-proof is the gate in front of it.
- **Max-lane cap:** `MAX_LANES` (v1: 2, per Musa op19217) — the autoscaler never spins beyond it; no unbounded spin / token-burn. A separate absolute kill-switch (`desired_state`-level disable) stops all autoscaler action instantly.
- **PROTECTED-from-auto-kill set:** coord, any client-poller lane, any money-path lane, singletons — hard-excluded from the wind-down candidate query by construction (allow-list of auto-killable lanes, not a deny-list).
- **Atomic check-and-claim** (§4) on every spin AND kill.
- **Fail-safe:** on ANY ambiguity (can't prove idle, can't claim, signal source unreadable, cap unknown) → DO NOTHING (never spin, never kill). Detection + a degrade-alert stay ungated so a safety page is never silenced by autoscaler state (mirrors the SRE watchdog shape).

## 6. Rollout (ships INERT, like the sentry watch)
1. **INERT phase:** deploy detect+log only — computes demand, would-spin/would-kill decisions written to a log/table, ZERO actuation. Run it against live signal for a soak window.
2. **Wet-prove:** show the INERT decisions match reality (correct spins/kills it *would* have made, protected set never selected, cap respected, idle-proof correct) → to Nazim's gate.
3. **Arm spin-only** (supervised: propose→confirm) after his gate. Then **arm wind-down** separately after another soak+gate. Never spin+kill armed in one step.
4. Keep Gazzabyte informed of token-burn as the pool ramps (via coord's operator relay).

## 7. Build delegation
Hub stays responsive; the BUILD is delegated to a dedicated lane/subagent from THIS design once gated. The hub owns the design, the gate interface, and the arming decision — not the inline coding.

## Open questions for Nazim's gate
1. Canonical demand-signal source (§2: coord-dispatch queue vs unclaimed agent_messages)?
2. `MAX_LANES`=2 and `SPIN_THRESHOLD`=1 correct for v1?
3. Is `tabung-jumaat` (once on gzb) a STANDING lane the autoscaler must treat as protected, or does it become an elastic pool member?
4. Where does the INERT decision-log live (a `fleet_lane_autoscale_log` table on substrate)?
