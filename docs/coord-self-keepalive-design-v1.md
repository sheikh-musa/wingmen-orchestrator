# Coord self-keepalive / idle-ghost auto-recover — design v1 (for orch-console's gate)

**Context:** Nazim greenlit (#40205) prioritizing this after 3 manual coord resets in one day on the live-client irsyad workstream. Directive: "build the mechanism, don't keep hand-resetting," with 5 hard safety guards.

## KEY FINDING — the mechanism already EXISTS and is guard-complete; it is just NOT DEPLOYED/ARMED

`nervous_system/lane_wedge_watchdog.py` already implements this exact detector+recover (built for the 2026-07-29 cai 6-hour idle-wedge). It ships as a one-scan CLI, **detect-only by default, and is NOT scheduled anywhere** (no cron, no systemd, not in boot_fleet_health) — which is precisely why coord's wedges go unrecovered and need manual `reset_lane`. So the work is **deploy + arm** (+ resolve one topology question), NOT a from-scratch build.

## The 5 guards → already satisfied by existing features

| # | Nazim's guard | Existing mechanism |
|---|---|---|
| 1 | **Fail-safe on subagent** — never act if a subagent runs / can't confirm none | `composer_capture.sh` detects `Waiting for N background agents` → `CC_BUSY=1` → treated as working, NO action (the exact 07:05-class protection). PLUS lane reset is **git-clean-guarded** → never destroys uncommitted work. |
| 2 | **Detect precisely** (idle + staged-unsubmitted + no subagent + grace window, not mere silence) | Wedge signature = (A) unread bus rows older than `UNREAD_MIN_AGE_SEC` + (B) no self-write within `QUIET_SEC` + composer staged/ghost via composer_capture. Grace windows built in. |
| 3 | **Recover minimally** (submit staged / benign nudge, NOT reset unless N fails) | `--arm=nudge` = ghost-aware count-only nudge, once per episode, **never clobbers REAL staged text** (the 07-04 money-migration phantom guard). `--arm` (escalate) = reset_lane ONLY if still wedged after the nudge. |
| 4 | **Attributable + bounded** (log, cap, alert on repeat) | Logs each classification; once-per-episode nudge cap; `--alert` pages the operator on wedge/repeat; deadman pages if the watchdog itself goes blind. |
| 5 | **Singleton-safe** (engineer lanes only) | Lanes → reset (git-clean-guarded); SINGLETONS (cai/hub/SRE) → nudge/PAGE only, NEVER auto-reset. Singleton nudge delegated to the sanctioned tool (nudge_cai). |

Net: no guard is missing. The design is how to turn it on safely.

## PROPOSAL — deploy + phased arm (mirrors the autoscaler INERT-soak-then-arm pattern)

1. **Deploy** `lane_wedge_watchdog.py` as a periodic runner (cron every ~5–10 min) scoped to **gzb ENGINEER lanes** (coord/scholar/tabung/…); explicit singleton denylist (cai, cc-fleet-health/SRE, orch/hub, console/nazim).
2. **Phase 1 — `--alert` (detect + page, NO action):** soak to prove detection precision on real wedges (correctly fires on coord's idle-staged wedge; correctly STAYS SILENT on busy/subagent/actively-working lanes). This is the "observe-first" gate you required for keepalive.
3. **Phase 2 — `--arm=nudge` (nudge-only recovery):** the minimal, safest recover — ghost-aware nudge, never resets, never clobbers real staged text. Expected to clear coord's flaky-Enter idle-ghost wedges without any reset.
4. **Phase 3 (optional) — `--arm` (full ladder):** add the reset_lane escalation (git-clean-guarded) ONLY if nudge-alone leaves lanes wedged. Kept off unless proven necessary.

## THE ONE REAL OPEN QUESTION (needs your gate) — recovery-host authority

The recover ACTION must run on the lane's **host** (coord/scholar/tabung wedge on **gzb**), but the watchdog's recovery is `fleet_health_lease`-gated (default holder = cc-fleet-health, on the **Mini**) — and the Mini SRE cannot `tmux` gzb lanes. Options:
- **(A, recommended for v1)** Run a **gzb-scoped instance under the HUB's lane-lifecycle pen** (the hub already owns its own gzb engineer lanes — coord is a hub client lane by your own #40072). Detection stays host-agnostic; recovery is hub-authority for gzb lanes only. Mini lanes stay the SRE's.
- **(B)** Keep it purely `fleet_health_lease`-gated and give the SRE a gzb reach — heavier, cross-host tmux.
Your call on the authority model. Everything else is settled by the existing code.

## Interim
Keep manual `reset_lane`-recovering coord (per your instruction) until this ships. This does NOT block the enrol co-verify — you own that live regardless of coord.

## Open tunables for your gate
Grace window (UNREAD_MIN_AGE_SEC / QUIET_SEC — 10–15 min?), per-lane retry cap + repeat-trip page threshold, and phase-1 soak duration.
