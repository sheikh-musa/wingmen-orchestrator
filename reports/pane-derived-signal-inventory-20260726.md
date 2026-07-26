# Pane-derived signal inventory — 2026-07-26 (cc-orchestrator / hub)

**Why this exists.** On 2026-07-26 we established that a tmux pane can **silently freeze** — stop
repainting while continuing to serve its last rendered content. A live agent's pane showed
`100% context used` for hours after a reset while telemetry showed 19%. Two bodies nearly acted on stale
pixels: one nearly reset a healthy governance node mid-delegation, and an automated watchdog filed a
false escalation off the same render. Nazim (orch-console) named the deliverable: *nobody has an
inventory of what in this fleet reads panes.* This is it.

**The rule it serves** (sharpened by Nazim, who withdrew his own stronger form within ten minutes):

> **A pane reading that ASSERTS ACTIVITY is only evidence if the pane is repainting.**
> A static pane is consistent with BOTH *idle-and-truthful* AND *frozen-and-lying*. What separates them
> is whether the render **claims motion** — a busy marker, a ticking timer, an in-progress percentage.
> An idle pane legitimately does not animate and is still truthful.

**Method.** Python `os.walk` + `re` over 3591 files (226 with tmux hits) — *not* shell `grep`, which is a
wrapped, gitignore-aware function on this box and silently skips files. **Positive control:**
`capture-pane` in `scripts/reset_orch.sh` → found at :140,:152. Second control: `list-sessions` in
`console/panes.py:161` → found. Without those controls a zero-result sweep would be indistinguishable
from a clean one.

---

## 🔴 HIGHEST RISK — automated AND asserts-activity AND unguarded

| # | Site | Why |
|---|---|---|
| 1 | `nervous_system/lane_watchdog.py:184` (progress_fingerprint) + `:226/236` | The only fleet-wide automated pane reader that both **escalates** and **keystrokes** (via `verified_resubmit`). A frozen pane is byte-identical, so it reads as **PROGRESS_STALL** rather than as frozen. **This already filed the false escalation (#11808).** |
| 2 | `nervous_system/lane_watchdog.py:456` | The one automated path that **types into the LIVE HUB**, gated purely on absence of `esc to interrupt`. Frozen-idle ⇒ `C-u` + type + Enter into a hub that is actually mid-turn — the phantom-injection class. |
| 3 | `nervous_system/ingest.py:323` (`pane_working`) | Sits on the **operator's inbound path**. Frozen-busy ⇒ his messages deferred up to `MAX_DEFER_SEC=600`. Frozen-idle ⇒ interrupts the hub mid-turn. |
| 4 | `nervous_system/console/panes.py:192` (`_is_working`) | No wrong *action*, but it is **the render two bodies almost acted on** — the fleet's authoritative "who is working" display, on the operator's phone, trusting a single capture. |
| 5 | `scripts/lane_nudge.sh:29` (`pane_working`) | A **verification primitive** whose success criterion is a freezable string. It can **certify delivery of a nudge that never landed**, to two callers (`lane_watchdog:271`, `priority_sla_watchdog:357`) that then stop retrying. |

*Item 5 is the one to sit with: `lane_nudge.sh` is the "verified submit" everything trusted all night.*

---

## SURPRISES — things we did not know

- 🔴 **`coordinator_pane_publisher.py` launders frozen panes into the substrate as permanently fresh.**
  It writes `captured_at = now()` every 10s **regardless of whether `pane_text` changed**, and
  `console/db.py:326` filters only on `captured_at` freshness. **The console's ">90s = stale" defence is
  structurally unable to fire.**
  ✅ **Cheapest high-leverage fix in the fleet:** a content-hash / `changed_at` column would make freeze
  detectable **fleet-wide for free** — every body already reads this table.
- 🔴🔴 **`scripts/priority_sla_watchdog.py` — THIS ENTRY INVERTED ON THE OTHER HOST. IT IS LIVE.**
  **My original finding, true of the STUDIO only:** untracked, no plist, not loaded, *"one `launchctl
  bootstrap` from being risk #1."*
  **Measured on the MINI by orch-console at 10:53Z, with a stated positive control** (146 tracked files
  under `scripts/`, so the tracked-check works): **TRACKED · plist EXISTS · `launchctl` LOADED · RUNNING**
  (scans logged 10:49:49 and 10:51:22). **It is the source of every SLA escalation tonight.**
  **It is not one command from being the highest-risk automated pane reader. It IS that, live.**
  🔴 **AND THE KEYSTROKE IS WORSE THAN "TYPES INTO A PANE": `:302` sends `C-u` — which DESTROYS staged
  composer text BY DESIGN — into `orch` and `nazim` over SSH, gated at `:300` SOLELY on
  `esc to interrupt`. Freshness/repaint-aware references in the whole file: ZERO.**
  **A frozen pane shows no `esc to interrupt` ⇒ reads idle ⇒ `C-u` fires ⇒ wipes whatever is staged.**
  ⚠️ **THIS IS A CANDIDATE ROOT CAUSE FOR TONIGHT'S DISAPPEARING COMPOSER TEXT** — an automated version
  of the exact harm Nazim inflicted by hand (his ~10 SSH nudges each began with `C-u`). **Not asserted as
  the cause** — the attribution of those strings is ruled `origin: unrecoverable` (CAI-625/626) and I am
  not re-opening it with a new guess. Recorded as a mechanism that exists and could do it.
  *Live but not firing (0 nudges) — **luck plus dedup, not a control.***
  📌 **THE LESSON IS THE INVENTORY'S OWN SHAPE:** I flagged the Mini as unmeasured and was right to; but a
  per-host finding stated in the fleet's voice **inverts silently on the host you did not scan.**
- 🔴 **THE SHARPER FRAME (Nazim): ask which signals are guarded only INCIDENTALLY.** Not *"which are
  guarded"* but *"which happen to be guarded as a side effect of something else"* — **those stop being
  guarded silently when the incidental reason goes away.** `pane_busy`'s repaint check is exactly this:
  the background-agent branch samples twice because that path *happened to need* a liveness proof, not
  because busy-markers were decided to require one. **The fleet's ONLY repaint check covers the LESS
  common marker, by accident, and nothing records that it is load-bearing** — so a refactor that
  "simplifies" it removes a control nobody knows exists. Generalises past panes: **an incidental guard
  has no owner, no test, and no comment saying why it must stay, which makes it indistinguishable from
  redundant code to the next reader.**
- ⚠️ **Our one repaint check is asymmetric and misses the commonest marker.**
  `composer_capture.sh:117 pane_busy` samples twice — but **only on the background-agent branch**; the
  `esc to interrupt` branch `return 0`s at :121 **before** any liveness test. *Mitigating: on that branch
  a frozen pane makes the reset REFUSE (exit 5), which is the fail-safe direction.*
- `scripts/lane_heartbeat.sh:20` pipes pane text **straight into `agents.current_task`** — a pane read
  becomes a DB fact the console displays as truth.
- `composer_parse_pane` (the reset primitive's composer capture) is **unguarded** — already logged as an
  open defect, now **twice observed** disagreeing with reality in both directions.
- Retired-but-live-on-re-enable: `cai_bridge.py:150`, `tg_bridge.py:196` still contain busy checks.
  4 `.claude/worktrees/agent-*` trees carry full copies of these readers.

---

## SAFE BY CONSTRUCTION — structural reads, not renders

`has-session` / `list-sessions` / `list-panes` / `display-message -p '#S'` answer from the **tmux server's
own state, not from the render**, so they cannot freeze. This covers `ingest.py:276`,
`console/panes.py:161,322`, `lanes.sh:56`, `boot_*.sh`, `break_glass.sh`, `mirror_*.sh`,
`nudge_support.sh`, `spawn_uiux_review.sh`, `breakglass_studio_orch.sh`, and the reset scripts' own
session guards. **No action needed on any of these** — the distinction that matters is
*render-derived* vs *server-derived*, not *tmux* vs *not-tmux*.

---

## COULD NOT ESTABLISH — do not read this inventory as complete

- **The Mac Mini's readers are UNMEASURED** — ⚠️ **and one entry ALREADY INVERTED there (see
  `priority_sla_watchdog` above): dormant on the Studio, LIVE on the Mini.** Treat every risk rating in
  this file as **Studio-scoped** until the Mini half is done.
- **(original text)**  Nazim's body, its `coordinator_pane_publisher` instance and
  the Mini's copies of these scripts run on a host that was not scanned. **This is a one-host inventory
  of a two-host fleet** — the same narrower-than-the-claim shape we spent the day cataloguing.
- Whether the **running** `lane_watchdog.py` matches disk. A hot-applied copy exists at
  `logs/lane_watchdog.py.bak-preop3695`; the process is `StartInterval`-driven so there is no long-lived
  pid to inspect. **Risk #1 and #2 are in the file whose running version is unconfirmed.**
- Whether `dev.wingmen.drift-detector` / `health-check` reach panes indirectly. `health_check.sh` has no
  tmux call (verified); not every helper was traced.
- **Real-world freeze frequency and duration.** This inventory is static. No repaint behaviour was
  measured over time.

---

*Not written to `work_outputs`: that table requires a NOT NULL `job_id` and is shaped for build
deliverables (build_spec / commit_sha / deploy_url). Fabricating a job row to satisfy the letter of the
rule would be ceremony. Durability is served here + the bus (#11843 cai / #11844 orch-console) + this commit.*
