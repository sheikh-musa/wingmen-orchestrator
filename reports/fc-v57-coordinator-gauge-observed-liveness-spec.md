# fc-v57 coordinator-card gauge — observed-liveness staleness (spec)

**Author:** cc-fleet-health (SRE) · **For:** orch-console (build + gated deploy) · **Reviewer:** cc-quality
**Context:** #25645/#25647/#25648. The fc-v56 gauge fix (downed/recycled/offline + sub_tag=NULL) is deployed, but cc-quality Finding 1 (b3043a50) showed the operator's "Quality frozen 95%" tile won't clear from a display fix alone: the coordinator card gates staleness ONLY on session-supersession, and on-demand coordinators (cc-quality) don't emit a `cc_session_costs` row on wake, so their freshest row is never superseded → 95% shows indefinitely.

## Signal source (reuse, no new mechanism)
`agent_observed_activity(agent_id text, last_observed_at timestamptz)` — the view I built (migration 054): each agent's most recent `agent_messages` row. Already the liveness truth for `priority_sla_watchdog` (054), `commitment_sweeper._live_agents`, and the autoscaler body-selection. This makes the console gauge its **4th consumer** — the *observation > heartbeat/supersession-column* principle, no twin-drift. cc-quality reads offline/heartbeat-less BY DESIGN (CAI-729), so its **bus activity is the only liveness truth** for it.

## Query change
Wherever the coordinator card resolves its context % from the identity's freshest `cc_session_costs` row, `LEFT JOIN agent_observed_activity ON agent_observed_activity.agent_id = <coordinator cc_identity>`, carrying `last_observed_at` alongside the cost row's `ended_at` + `latest_context_tokens`.

## Grace window
`LIVE_WINDOW = 20 min` — **keep identical** to `context_health_watchdog._STALE_S` (`CTX_WD_STALE_MIN`, default 20) so the console and the watchdog agree on "is this body currently live." If that env changes, both should read the same value.

Two booleans on the freshest cost row:
- `cost_fresh` = it is the identity's **newest** session (existing supersession check) **AND** `ended_at > now() - LIVE_WINDOW` (recency). A superseded OR >20 min-old row is NOT fresh.
- `bus_live` = `last_observed_at IS NOT NULL AND last_observed_at > now() - LIVE_WINDOW`.

## 3-state render rule (evaluate top-down)
1. **`cost_fresh`** → show the numeric **%**. *(normal — the row reflects current context.)*
2. else if **`bus_live`** → show **"context —"**. *(alive — posted to the bus within 20 min — but the cost row is stale/absent, so we do NOT have a current number; showing the stale % is the frozen-95% bug. This is a working on-demand coordinator.)*
3. else → show **OFF / downed**. *(no fresh cost row AND bus-silent >20 min = genuinely down. This is cc-quality NOW → clears the frozen 95%.)*

Why this beats a raw staleness threshold (your #25645 worry): state 2 means a live-but-quiet coordinator is **never** shown OFF and **never** shown a stale number — it's always state 1 or 2 while alive. The threshold can't false-drop it.

## Edge cases
- `last_observed_at IS NULL` (never bused) → `bus_live=false` → state 3 (OFF) when not `cost_fresh`. A body with no fresh cost row that has never bused is not demonstrably alive.
- Apply to **both** paths: the server-attached irsyad view (`app.py` ~1416 `ctx_by_sess`) **and** the fleet-wide client gauge (`fleet.js` `laneCtxIndex`). Do the JOIN server-side and pass the resolved state to `fleet.js`, or pass `last_observed_at` through and let `fleet.js` apply the same rule — either way one rule, no twin.
- **sw.js VERSION bump** in the same diff (PWA serves stale bundle otherwise).
- Heartbeat bodies are unaffected (they're `cost_fresh` while working via supersession); this is the coordinator/on-demand path.

## Verification (for cc-quality's review)
- **cc-quality now**: freshest cost row (~95%, ~10 h old, not superseded) → not `cost_fresh`; `last_observed_at` ~10 h → not `bus_live` → **state 3 OFF** (was frozen 95%). ✓
- **A live coordinator** (a bus row <20 min, but its cost row is stale) → **state 2 "context —"**. ✓ (not a false OFF, not a stale number)
- **A normally-working body** (fresh cost row) → **state 1 %**. ✓

## (b) — secondary, later (not this spec)
Making a *working* on-demand coordinator show its **live %** (not just "context —") needs the `cc_session_costs` **auto-writer** (`nervous_system/cc_session_costs_auto_writer.py`, launchd sweep of CC jsonls) to pick up the new session promptly on wake — a sweep-kick on the coordinator's wake path, NOT a boot-script line. Only helps state 2 (turns "context —" into a live number). Deferred; state-3 OFF already clears the operator's flag.
