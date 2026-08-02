# Lane-wedge watchdog

`nervous_system/lane_wedge_watchdog.py` — detects the **idle / stopped-draining
wedge**: an agent that is alive and still holds its lease but has stopped
consuming its bus inbox (the 2026-07-29 cai incident: ~6h idle, a live money-path
grant stalled, every other gauge read it green).

## How it decides (all must hold)
- **Signal A (substrate, host-agnostic):** unread `agent_messages` older than
  `UNREAD_MIN_AGE_SEC` (20m) but not chronic backlog (`< UNREAD_MAX_AGE_SEC`, 6h),
  AND the agent has not itself written to the bus within `QUIET_SEC` (20m).
  **exclude-answered:** a row is not counted if the recipient stamped `responded_at`
  OR wrote any later bus message — bodies act by REPLYING, not stamping (#12486), so
  handled rows would otherwise read as phantom backlog.
- **Signal B (composer):** the tmux composer is empty or a dim ghost (safe to
  nudge). REAL non-dim staged text ⇒ the agent has its own draft ⇒ **alert only,
  never nudged.** For singletons (cai/hub/SRE) Signal B is delegated to the
  sanctioned nudge tool's own ghost guard.
- **Not working (skip-if-working):** a pane whose footer shows a live turn
  (`esc to interrupt`, via `composer_capture.sh` `CC_BUSY` — the SAME check
  `lane_nudge.pane_working()` uses) is mid-inference, **not** wedged. A long
  high-effort turn writes nothing to the bus while it thinks, so Signal A alone
  misreads it as idle. Applied to lanes and to any singleton with a locally-readable
  pane (`SINGLETON_SESSIONS`). Fail-safe: suppress only on a POSITIVE live read, so
  an unreadable pane never hides a real wedge. (Nazim 14067/14103/14470.)
- Confirmed only after `WEDGE_MIN_POLLS` (4) stable scans **and** `WEDGE_GRACE_SEC`
  (300s) — ~5 min of a stable wedge before any action.

## Page only a GENUINE stall (nudge is cheaper than a page)
A confirmed wedge is always **logged** and (when armed) **nudged once per episode**,
but it only **pages the operator** — and only counts toward the repeat-wedge breaker
— if it is a *genuine* stall: fully quiet past `ALERT_QUIET_SEC` (90m) **OR** holding
an **actionable** (`requires_response=true`) unread. A lane merely cycling between
short tasks (wrote to the bus more recently, only FYI-grade unread) is nudged but
never paged, and its benign idles never trip `REPEAT_K`. This keeps the real-stall
catch (the 2026-07-29 money-grant was a req=True stall) while dropping the benign
idle-between-tasks chatter. A `WEDGE_UNSAFE` (real staged draft) always surfaces —
it cannot be auto-nudged. (Nazim 14413/13969/14033.)

## Modes (staged arm — ship the safest, promote deliberately)
1. **detect-only.** `--alert` surfaces a wedge / repeat / watchdog-down gap. Touches
   no agent.
2. **`--arm=nudge` (SHIPPED — op#8807 stage-2, 2026-07-31).** Also fires the
   ghost-aware count-only nudge (`nudge_cai.sh` / `lane_nudge.sh` / hub tmux), once
   per episode. This is what the launchd plist ships now (`--alert --arm=nudge`).
   Stops at nudge — escalate/reset is NOT armed.
3. **`--arm` (=escalate).** Also, if still wedged after the nudge: `reset_lane.sh`
   for lanes (git-clean-guarded); **PAGE** for singletons (cai/hub/SRE are never
   auto-reset). Repeat-wedge (`REPEAT_K` in the window) stops auto-acting + pages.

**Alert path (`--alert`) = the BUS, not Telegram.** On a wedge / repeat /
watchdog-down gap, `_page()` emits an **attributable `agent_messages` row from
`cc-fleet-health` to `orch-console`** (P1, `requires_response=false` +
`responded_at` stamped so it never re-triggers the SLA false-stall flood). Nazim's
console voices it to the operator. This is the SRE's sanctioned channel (charter
§5) — it is deliberately NOT `nazim_send.sh` (Nazim's own Telegram voice, which
also fails silently off the hub host — that is why detect-only alerts used to reach
only a log file). Deduped once-per-episode. A failed emit logs `ALERT-DELIVERY-
FAILED` LOUDLY (dead-man's-switch) rather than silently.

Recovery **actions** are lease-gated (`fleet_health_lease`, CAI-RESP-501,
fail-closed for a non-holder — the scan then downgrades to detect-only and still
surfaces). Detection + the bus alert are ungated. It never auto-submits real staged
text (the 07-04 phantom-injection class); the nudge is a count-only line.

## Promote through the stages
Edit `launchd/dev.wingmen.lane-wedge-watchdog.plist` `ProgramArguments`, then
`launchctl unload/load` it — **after review, from a lean context, and confirm
`LANE_WEDGE_EXCLUDE` protects demo-critical lanes:**
1. add `<string>--arm=nudge</string>` → auto-nudge only.
2. later swap to `<string>--arm</string>` → full ladder.
Verify the lease-gate is stable (not flapping armed↔dry) before step 2.

## Operate
- One scan: `.venv/bin/python3 nervous_system/lane_wedge_watchdog.py --once --json`
- Offline proof: `… --self-test` (no DB, no tmux). Unit tests:
  `tests/test_lane_wedge_watchdog.py`.
- Logs: `logs/lane_wedge_watchdog.log`; heartbeat: `logs/lane_wedge_watchdog_heartbeat`.
- Tunables (env / plist `EnvironmentVariables`): `LANE_WEDGE_UNREAD_MIN_AGE_SEC`,
  `LANE_WEDGE_QUIET_SEC`, `LANE_WEDGE_MIN_POLLS`, `LANE_WEDGE_GRACE_SEC`,
  `LANE_WEDGE_STAGE2_DELAY_SEC`, `LANE_WEDGE_REPEAT_K`, `LANE_WEDGE_EXCLUDE`,
  `LANE_WEDGE_SINGLETONS`, `LANE_WEDGE_ARM`, `LANE_WEDGE_TMUX`, `LANE_WEDGE_HUB_SSH`.

> Note: an earlier composer-only prototype lives at `scripts/lane_wedge_watchdog.py`
> (untracked). It is **superseded** by this bus-driven module — the plist points
> here now. Safe to delete once you've confirmed this one.
