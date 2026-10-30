# Zero-waste fleet audit — op#35929/35930 (Musa directive, bus #83575)

**Window measured:** 2026-10-29 ~08:31Z → 2026-10-30 06:10Z (~24h, DeepSeek coverage 21.6h — see gaps below).
**Method:** live Claude Code transcript parsing (sum `usage.input/output/cache_creation/cache_read` tokens per message, hourly-bucketed) for every tmux pane found on both hosts (Mini local + `ssh gzb`), cross-referenced against `agent_status`. DeepSeek $/day from consecutive balance deltas in `deepseek_balance_poll.py`'s history file. Wake counts and reply counts from `agent_messages` (`to_agent`/`from_agent`) in the same window. Tool: `scripts/zero_waste_audit.py` (this PR).

Every number below is a direct measurement or an explicitly-labeled extrapolation. Two measurements failed outright and are reported as such, not guessed (see **Measurement gaps**).

## 1. Per-lane 24h token burn, ranked

| Lane (tmux/base agent) | Host | 24h tokens | msgs | avg tok/hr | wakes in (24h) |
|---|---|---|---|---|---|
| **cc-fleet-health (me)** | Mini | 1,168,921,619 | 4,793 | ~48.7M | 219 |
| cc-cosem-exams-1 | Mini | 785,311,110 | 7,371 | ~32.7M | 215 |
| cc-cosem-adcda-2 (urgent) | Mini | 618,778,476 | 2,680 | ~25.8M | 48 |
| cc-cosem-adcda-1 | Mini | 535,766,014 | 3,342 | ~22.3M | 63 |
| cc-cosem-platform-2 (calendar) | Mini | 521,434,895 | 2,256 | ~21.7M | 47 |
| cc-cosem-exams-3-1 | Mini | 509,242,155 | 2,223 | ~21.2M | 101 |
| cc-cosem-adcda-3 (split) | Mini | 453,035,851 | 1,803 | ~18.9M | 47 |
| cc-cosem-exams-2-1 | Mini | 449,053,080 | 1,912 | ~18.7M | 70 |
| cc-wingmen-sites-1 | Mini | 382,623,262 | 1,626 | ~16.0M (front-loaded, idle since 14:55Z) | 28 |
| cc-cosem-platform-4 (calendar2) | Mini | 282,468,084 | 1,222 | ~11.8M | 45 |
| cc-cosem-platform-1 (author) | Mini | 185,906,064 | 891 | ~7.7M (idle since 09:44Z) | 20 |
| cc-hifz-1 | Mini | 221,363,412 | 959 | ~9.2M | 23 |
| cc-quality | Mini | 82,459,395 | 427 | ~3.4M | 144 |
| orch-console (nazim) | Mini | 66,882,150 | 526 | ~2.8M | **917** |
| cc-hc95-broker-1 | Mini | 38,757,105 | 361 | ~1.6M | 10 |
| cc-bariq-1 | Mini | 39,031,300 | 303 | ~1.6M | 14 |
| cc-oeh-1 | Mini | 36,800,092 | 367 | ~1.5M | 10 |
| cc-loop-1 | Mini | 31,785,390 | 231 | ~1.3M | 2 |
| cc-paperclip-spike-1 | Mini | 44,866,358 | 139 | ~1.9M | 7 |
| cc-coffeemedia-1 | Mini | 3,506,578 | 21 | ~0.15M | 3 |
| cc-substrate-1 | Mini | 4,538,564 | 29 | ~0.19M | 2 |
| cc-orchestrator (hub) | gzb | 15,247,066 | 132 | ~0.64M | 124 |
| cc-cosem-convergence-2 | gzb | 10,206,548 | 115 | ~0.43M | — |
| cc-ffsfulfilment-1 | Mini | 0 | 0 | 0 (dormant) | 0 |
| cc-cosem-tdu-coord-1 | Mini | 0 | 0 | 0 (dormant) | 0 |
| cc-finance-1 | Mini | 0 | 0 | 0 (dormant) | 0 |
| cc-mamadah-1 | Mini | 0 | 0 | 0 (dormant) | 0 |
| cc-second-brain-1 | Mini | 0 | 0 | 0 (dormant) | 0 |
| cc-irsyad-5/1/2, cc-scholar-1, bayanqa | gzb | 0 each | 0 each | 0 (dormant) | 0 |

**DeepSeek:** ~$2.68 spent over 21.6h of measured coverage → extrapolated **~$2.98-3.00/day**, well under the $7.80/day cap (op#35703). No DeepSeek waste found this window.

## 2. Measurement gaps (honestly reported, not guessed)

- `gzb:irsyad-coord` — transcript exists but `ssh gzb cat <file>` timed out after 30s (file too large to pull whole over this link in the budget this tool used). **Could not measure.** Fix: stream/tail remotely instead of `cat`-ing the whole file, or bump the timeout — flagging as a tooling gap, not a lane finding.
- `gzb:encoder` — no Claude Code project transcript found for that pane's cwd (`/home/gazzai`) — likely a plain shell pane, not a Claude lane. Excluded, not a gap.
- DeepSeek balance history only has 21.6h of coverage (23 points back to 10-29 08:31Z), not the full 24h — the $/day figure above is extrapolated from the covered portion, labeled as such.

## 3. Waste ranking, with evidence

### #1 — False/duplicate wakes (confirmed, partially already fixed this session)

**orch-console: 917 wakes in 24h** is the single largest wake-count outlier in the fleet by ~4x over the next body (me, 219). Root cause (confirmed earlier this session): `nervous_system/nazim_bus_notify.py` nudged on a POLL-time unread count without rechecking at SEND time, so a message already read between poll and send still fired a wake. Fixed in **PR#649** (merged `39a49ab`), live since 2026-10-29 21:52:24Z. Direct before/after evidence from the daemon's own log (`logs/nazim-bus-notify.log`), equal-length windows either side of the restart:
- Pre-fix (93min): 26 real nudges sent, 0 possible catches (mechanism didn't exist).
- Post-fix (114min): 12 real nudges sent, **46 would-be false wakes caught and suppressed** by the new recheck.

That fix only covered one dispatcher. This directive's **PR#654** (this run) generalized the identical recheck-at-send-time pattern to three more dispatchers that shared the exact same bug shape: `cai_bus_notify.py`, `fleet_health_bus_notify.py` (mine), `finance_bus_notify.py` — each was firing an unconditional nudge even on a live unread count of zero. 12 new tests, 35/35 passing. Other dispatchers checked (`priority_sla_watchdog.py`, `lane_wedge_watchdog.py`, `wake_backstop_sweep.py`, `idle_with_work_watchdog.py`) are single-pass scripts that act on live state within one invocation — not the same root cause, left as-is.

**Residual:** orch-console's 917/24h is measured AFTER the PR#649 fix went live (it covers the tail end of the pre-fix period and all of the post-fix period) — the true post-fix-only rate is lower than this blended number suggests, but still high. Recommend a follow-up measurement in 24h, after PR#654 is also merged, to see the clean post-both-fixes rate.

### #2 — My own (cc-fleet-health) wake/turn pattern (confirmed, NOT code-fixable — a process finding, named honestly)

4,793 messages in 24h for a single body, averaging ~48.7M tokens/hr, is the highest total AND the highest per-wake overhead in the fleet. The underlying cause isn't a dispatcher bug: it's that a large share of my 219 inbound wakes this session were routine, single-row, zero-judgment items (a repeated GLM-quota alert at >90%/0%-pace, fired hourly by design per #80981 — 7 identical repeats observed tonight alone) — and **each one cost a full fresh conversational turn**, resending the entire (and continuously growing) system prompt + conversation history just to mark one row read. The dispatcher-side fix (#1 above) reduces how many of these arrive; it does not reduce the cost of the ones that still do, because that cost is paid in MY turn-taking pattern, not in dispatcher code.

This is **not code-enforceable** the way the other three items are — there's no script to patch; it's an operating-pattern cost inherent to a reactive, per-message-turn harness loop. The two real levers: (a) the dispatcher fixes above (fewer arrivals), and (b) continuing to batch/verify rather than react-per-message where safe (already my practice this session, but worth naming as the actual mitigation here rather than pretending a 4th code fix exists for it).

### #3 — Base-id vs instance-id addressing inconsistency (confirmed gap, previously known, still open)

`agent_messages` shows wake/reply counts split across both a lane's base id and numbered-instance id for the same underlying body in several places this window (e.g. `cc-cosem-exams` vs `cc-cosem-exams-1`, `cc-loop` vs `cc-loop-1`, `cc-cosem-platform` vs `cc-cosem-platform-2`/`-4`). This under/over-counts per-lane wake totals in this report by an unknown amount (the split is real traffic, not double-counted — but it means the single "wakes in" column above understates a few lanes' true total by whatever landed on the sibling id). This is the same gap already tracked in my own memory (`bus-send-default-identity-still-base-not-instance`) and `held_commitments`/backlog — not new, not fixed by this directive's 3 builds, and not attempted here (out of scope for a 30h-window directive; flagging so it isn't lost).

### #4 — Idle-lane-still-polling: **checked, not found**

Every lane that measured 0 tokens this window (`cc-ffsfulfilment-1`, `cc-cosem-tdu-coord-1`, `cc-finance-1`, `cc-mamadah-1`, `cc-second-brain-1`, and the four dormant gzb/irsyad-pool lanes) also shows **0 wakes received** in the same window. No evidence of a lane burning cycles on idle polling while receiving no real traffic. Reporting this as a genuine negative result, not skipping it.

### #5 — Oversized-context-re-read / retry loops: no isolated additional case found beyond #2

Checked the highest-message-count cosem lanes (`cc-cosem-exams-1` at 7,371 msgs, `cc-cosem-adcda-1` at 3,342) for a retry-loop signature (many near-identical consecutive messages) — did not do a full content-diff pass (out of time budget for this window; would need per-message text comparison, not just token counts). Flagging as **not fully checked**, distinct from "checked, not found" in #4 — a fuller pass is a candidate follow-up, not a thing I'm claiming to have ruled out.

## 4. Code-enforced fixes shipped this directive

| # | Fix | PR | Status |
|---|---|---|---|
| 1 | Fleet-wide "no live unread = no wake" gate (generalizes PR#649 to 3 more dispatchers) | [#654](https://github.com/sheikh-musa/wingmen-orchestrator/pull/654) | Open, 35/35 tests, awaiting cc-quality review + console merge |
| 2 | Per-lane tokens/hour alarm (cross-host) | [#655](https://github.com/sheikh-musa/wingmen-orchestrator/pull/655) | Open, 30/30 tests — **threshold needs reconciling against this report's real data before merge, see below** |
| 3 | DeepSeek $/day cap alarm | (already existed, PR#630) — added 12 missing tests in #655 | Live; now tested |
| 4 | Context-size ceiling → checkpoint | — | Already auto-fires every 10min (`dev.wingmen.context-health-watchdog`, `--arm=amber`), confirmed live — no code change needed |
| 5 | Context-ceiling → auto-RESET arming conditions | [#653](https://github.com/sheikh-musa/wingmen-orchestrator/pull/653) | Docs-only proposal (`docs/zero-waste-reset-arming-proposal.md`). **cc-fleet-health never self-arms this — orch-console/cai take the arming decision to Musa, per the fleet-health charter's hard boundary.** |

### ⚠️ Action needed before merging PR#655: burn-alarm threshold is miscalibrated against real data

PR#655's tokens/hour alarm shipped with a 15M tok/hr threshold, reasoned from this session's earlier *spot-check* samples (idle=0, real work=0.5-3.65M/60min, a known-bad false-wake episode=81-93M/60min). This report's full 24h sweep shows that **legitimate, sustained active lanes already average well above 15M/hr** — e.g. `cc-cosem-exams-1` at ~32.7M/hr average, `cc-cosem-adcda-2` at ~25.8M/hr, several others in the 11-22M/hr range. As configured, the alarm would page orch-console continuously for lanes that are doing real, wanted work, not wasting anything. **Recommend raising the threshold** (something in the 50-60M/hr range sits above every measured legitimate average in this report, including my own 48.7M/hr, while still catching a repeat of the 81-93M/hr false-wake episode) **before this PR is installed**, or re-deriving it from a rolling per-lane baseline instead of one fixed fleet-wide number. Flagging this in the report rather than silently fixing it myself, since the PR is still open for cc-quality/console review.

## 5. Hermes decommission (separate directive, bus #83603, tracked separately)

Not part of op#35929/35930 — tracked and reported separately via its own bus thread once that task completes.

## 6. Sizing / what I built myself vs what's still open

Built and self-measured this directive (ops, reversible, in my pen): the measurement tool (`scripts/zero_waste_audit.py`, this report), and (via 3 parallel sub-builds under my own supervision) PR#654, PR#655, and PR#653's doc. Open/not attempted: the base-vs-instance addressing fix (#3 above — sizeable, pre-existing backlog item, not a 30h-window job), the full retry-loop content-diff pass (#5 — needs a per-message-text comparison tool I didn't build this window), and a clean post-both-fixes re-measurement (recommend re-running `scripts/zero_waste_audit.py --hours 24` 24-48h after PR#654 merges).
