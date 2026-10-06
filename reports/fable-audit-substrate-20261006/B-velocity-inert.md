# Fable substrate audit 2026-10-06 — Fork B: velocity, gates, queues, inert mechanisms, CI

Auditor: orch-console fork (Fable 5.1), READ-ONLY (substrate DB reads, local scripts/logs/git, `gh` on cosem/orchestrator repos only; ihsanos untouched per console guard). Window: 7 days to 2026-10-06 19:05Z unless stated. Labels: **verified** = measured; **inferred** = reasoned from measured data.

## Headline numbers

| Metric | Value |
|---|---|
| Open `requires_response` rows (7d) never stamped responded | **611** (55 <6h, 71 6-24h, 188 1-3d, **297 3-7d**) |
| …of those >60 min with NO reply anywhere (truly dropped) | **~157**, of which **~140 are P0/P1**; by recipient: cc-fleet-health 74 (73 P0/P1), cc-irsyad-coord 25 (22), cc-orchestrator 15 (15), cc-irsyad-coord-1 14 (9), cc-coffeemedia-1 8 (8), cc-second-brain-1 6 (3), cc-cosem-platform-1 5 (5) |
| …answered to the sender but never stamped (bookkeeping debt) | cc-fleet-health 95, cc-quality 58, cc-irsyad-coord 46, cc-substrate 15, cc-cosem-adcda 14 |
| Median answer time when answered (P1 rr) | orch-console 0.9 min · coord 3.1 · hub 3.4 · storefront 4.1 · quality 4.8 · fleet-health 5.7 · cosem-platform 6.7 · cosem-adcda 10.6 |
| coord_dispatch_queue throughput | created 15-26/day vs done 8-15/day (10-01..10-06) → backlog grows ~8/day; 27 HOLD rows (17 with no spec), 5 claimed-building |
| Autoscaler demand signal | logged `coord_queue_depth=0` every tick all day while **9 rows had `claimed_by IS NULL`** (ids 69,73,74,76,81,82,83,97,129) → spin never proposed |
| Lane utilisation signal | `agent_status`/history: **96% "working"** (6384/6643 rows, 24h); only `cai` ever reports idle → the gauge is liveness, not utilisation; no utilisation measurement exists |
| Operator ask ledger | open: 296 `ask` + **255 `captured` (untriaged)** + 292 `not_an_ask` still open + 26 waiting-on-operator; `client-asks-chase` and `captured-triage` both run **[DRY/observe]**: 147 chases due, 0 sent; 255 to triage, 0 sent |
| Bus volume | ~1,000-1,750 agent_messages/day; **~470/day addressed to orch-console** (1,038 P2 updates + 1,000 P1 updates in 7d) |
| Operator Telegram volume | ~190 inbound / ~250 outbound per day |
| CI | cosem-adcda & cosem-platform & wingmen-orchestrator: queue wait median/p90/max **0.0 min**; two self-hosted runners live on the Mini (`actions-runner` = ihsanos, `actions-runner-cosem`); ihsanos single heavy-build runner ~35 min/cycle ≈ **1.5 merges/h ceiling** (coord #54602, not measurable from here) |

## 1. Gate latency — the gates are fast; the LEDGER is broken (verified)

When a reply happens it is quick everywhere (medians 1-11 min). The velocity loss is not decision time; it is **rows that never get a stamped answer**, so every downstream watcher (SLA watchdog, wake backstop, idle-with-work, queue-age) sees a phantom backlog of 611 rows and the truly dropped ~157 hide inside it.

- Truly dropped P0/P1 (no reply in thread, no reply to sender within 6h): fleet-health 73, hub 15, coord 22 + coord-1 9, Tier-B lanes 16. fleet-health's 74 are overwhelmingly watchdog-generated rows (queue-age, wedge, model-apply) addressed to itself — self-paging that nobody closes.
- "Answered but unstamped": 95 (fleet-health) + 58 (quality) + 46 (coord) — the reply went out as a NEW row instead of `--reply-to`, so `responded_at` stays null forever. Every open RR >60 min since 09-30 04:19Z is still open.
- orch-console: 1,064 RR in 7d, all stamped, median 0.9 min — the console is **not** the bottleneck (coord confirms, #54602 §5).

## 2. Lane utilisation — unmeasurable today (verified)

`agent_status.status` is written at session-launch ("session-launch model=… repo=…") and stays `working`; 24 of 25 agents never report idle. `idle_with_work_state` has 2 rows (coord: 0 ticks; cc-irsyad-2: 1). So "snail's pace" cannot be seen in any gauge: a lane idle for 6 h with a full queue shows exactly like a lane mid-build. Tonight's cc-irsyad-1 "idle on the pool queue" (#54521) was only discoverable by asking.

## 3. Queue & ledger health (verified)

- `coord_dispatch_queue`: inflow exceeds outflow (10-01..10-06: 98 created / 71 done). 27 HOLD rows avg age 116 h, 17 with `spec_ref NULL`; before tonight's pass 9 rows sat claimable for 2-7 days (queue-age watchdog paged only from today, "closes the op#20774 gap").
- Pool eligibility admitted **0 of 32** open irsyad rows (worker prompt + poll filter: spec_ref non-null AND not money/minors/supervised) — so the elastic pool could never help irsyad by construction.
- `operator_asks`: `delegated_to` mixes lane slugs (`irsyad-coord`, `cosem-port`, 192+166 rows) with agent ids (`cc-cosem-platform`), so no join to the bus is possible; 286 open rows have no delegate; 292 rows judged `not_an_ask` remain open (never closed).
- `operator_backlog`: 23 `parked` rows, 12 untouched since 2026-08-07.
- `held_commitments`: 60 pending (coord 33, console 18, fleet-health 6), none overdue — this ledger is healthy.

## 4. Inert / disarmed mechanisms with live demand (verified unless noted)

| Mechanism | Mode now | Demand it is ignoring | Gate owner |
|---|---|---|---|
| `irsyad_autoscaler.py` (cron on gzbai, 20-min) | code default `inert`; hub says cron runs `--mode auto` (#54583) | probe returned depth=0 all day vs 9 unclaimed rows → **dead signal**; MAX_LANES=2 | hub (fix in flight, #54592) |
| `priority_sla_watchdog.py` → `client-asks-chase` | **[DRY/observe]** since added 2026-10-01 (3964bc1) | **147 chases due** (irsyad-coord-delegated asks), 0 sent | console |
| `priority_sla_watchdog.py` → `captured-triage` | **[DRY/observe]** | **255 untriaged captured asks**, 0 nudges | console |
| `sre_lane_recycle.py` (launchd `--no-arm`) | force-DISARMED red-reset (CAI-RESP-681 cond 5) | 798 refusals in the last 2,000 log lines; 9,230 hypothetical vs 1,261 acted lifetime | cai/console |
| `context_health_watchdog.py` | `--arm=amber` (checkpoint-only) | red (reset) leg dry-run by design (CAI-500) | cai |
| `hub_self_recovery.py` | `--mode observe` default; `HSR_MODE=act` gated on hc#177 (staleness watchdog) | hub dead-man never acts | console (hc#177) |
| `quality_gate.py` | `MODE_SHADOW` default; wired only into the console deploy gate | no product repo uses it | console |
| `front_door.py` | 0 references — dead code since 2026-07-22 | — | — |
| `lane_wedge_watchdog` | `--arm=nudge` (nudge only) | fine — acts | — |
| `queue_age_watchdog` | ARMED today; before today rows sat 8.5-12 h unpaged | — | fixed |

Pattern: six mechanisms exist, tick, and log "would" while the condition they guard is live. Each was shipped inert "pending soak" and the arming step was never scheduled (no `held_commitments` row for any of them except hc#177).

## 5. CI (verified for cosem/orchestrator; inferred for ihsanos)

No queue wait on the three repos I can read (0.0 min median/p90/max over 130 runs; durations 6-16 min). Two runner processes on the Mini (`/Users/ci-runner/actions-runner` and `actions-runner-cosem`). The ihsanos heavy-build runner is the serial resource coord identifies (~35 min/required cycle, builders cancelling each other's runs); with 3-4 builders now live it becomes the hard ceiling at ~1.5 merges/hour. A second ihsanos runner on gzb (or Mini) is the single largest throughput lever for irsyad.

## 6. Comms load (verified)

~470 bus rows/day land on orch-console, 2,038 of them `update` type in 7d — informational traffic that each costs a wake. Operator traffic ~250 outbound/day. Inferred: the console's wall-clock is spent reconciling updates, not deciding.

## Ranked fixes (highest leverage first)

1. **Stamp-or-die on RR rows** — `bus_send.py` requires `--reply-to` when answering an RR (fail-closed), plus a nightly sweeper that auto-stamps `responded_at` when a later same-thread message from the recipient exists, and pages per-recipient for truly dropped P0/P1 (the ~157). Evidence: 611 phantom-open rows; watchdogs chase ghosts. Owner: hub (bus_send) + fleet-health (sweeper). Effort: 1 day.
2. **Second ihsanos CI runner** (gzb or Mini) + cancel-on-supersede policy. Evidence: 1.5 merges/h ceiling, builders queueing. Owner: fleet-health. Effort: 2-4 h.
3. **Arm `client-asks-chase` and `captured-triage`** (observe → act with rate limits). Evidence: 147 due chases, 255 untriaged asks, 0 actions since 10-01. Owner: console (fleet-health runs it). Effort: 1 h.
4. **Autoscaler: fix depth probe, MAX_LANES 4, claim loop accepts `[supervised]` build-only rows, reaper honours console/coord HOLD.** Evidence: depth=0 vs 9 rows; 0/32 eligible; -2 reaped against a hold. Owner: hub/cc-substrate (in flight). Effort: ≤1 day.
5. **Real utilisation signal** — lanes stamp `idle` on empty inbox + no tool activity for N min (launcher hook), surfaced on the fleet console; idle-with-work pages on it. Evidence: 96% "working" gauge; idle only discoverable by asking. Owner: fleet-health. Effort: 1 day.
6. **One identity for delegates** — `operator_asks.delegated_to` and `coord_dispatch_queue.claimed_by` take agent ids only (CHECK constraint + backfill of slugs); close the 292 `not_an_ask` rows. Evidence: 358 slug rows unjoinable to the bus. Owner: console tooling (asks_*.py). Effort: 2 h.
7. **Arming calendar for every inert mechanism** — one `held_commitments` row per mechanism with the soak criteria and a date; `hub_self_recovery` act-mode and `sre_lane_recycle` arm decisions go to cai with the log evidence. Evidence: six dormant mechanisms, one tracked. Owner: console. Effort: 1 h to open; decisions after.
8. **Cut console wake load** — route `update`-type rows to a digest (one summary row per lane per 30 min) unless P0/P1 + rr; keep decisions/blockers/RR immediate. Evidence: ~470 rows/day on the console, 2,038 updates/7d. Owner: hub (bus_send/notifier). Effort: half a day.

Out of scope noted: `operator_backlog` parked rows from 2026-08-07 (12) look abandoned rather than parked — a triage pass, not a mechanism.
