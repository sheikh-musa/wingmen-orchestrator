# Console-fix batch (un-park) — done vs built

**Agent:** cc-console · **Branch:** `feat/war-room-live-feed` (Studio checkout, operator-facing) · **Build:** fc-v10
**Date:** 2026-07-12 · **Deploy:** operator owns — console NOT restarted. Route cc-uiux to verify, then redeploy (restart) to activate.

Un-parked from a ~38h stale draft. The favicon, NEEDS-YOU split, and peek-flowing-text were already DONE+verified (not touched). This batch actions the four remaining items from orch-console's dispatch (#7780/#7753/#7750/#3672).

---

## (a) 0-working bucket bug — ALREADY FIXED, now VERIFIED ✅ (no code change)
The current `_lane_bucket` + `_enrich_lanes_live` already derive working/idle/offline from the **LIVE tmux pane** (`panes.capture` → `_is_working`), not heartbeat age or the stale `agent_status.status` that read `active`/`task=None` for every lane. That's a **stronger** fix than #7780 asked for (it wanted "respect status=working"; the code reads the real pane).

**Verified live on the Studio** (where the operator's console + all lane sessions run):
```
working: 4  → cc-console, cc-cosem-platform, cc-cosem-platform-2, cc-shipforge (all genuinely working per pane)
idle:    3  → cai, cc-ihsanos, cc-infra (idle at prompt)
offline: 3  → cc-reviewer, cc-uiux ×2 (no live pane)
```
The count matches reality — the "0 working while busy" bug is gone. Nothing to build.

## (b) Peek into coordinators (#3672) — BUILT + verified ✅
Coordinator cards (orch + Nazim) were display-only. Now peekable like lanes, reusing the exact lane peek machinery.
- **db.py** `build_coordinators_query`: emits `tmux_session` (orch→`orch`, Nazim→`nazim`).
- **app.py** `_fleet_payload`: marks each coordinator `peekable` iff its session is in the **local** live-session set. `orch` is the hub session on this Studio host → peekable. Nazim's `nazim` pane lives on the **MacBook** → not local → `peekable:false`, card keeps its bus-activity view (no dead-end "peek ›" that would 404).
- **fleet.js** `coordCard`: emits `data-peek` + `.peek` box + "peek ›" only when `peekable`; bound by the existing `bindPeeks`, rendered by the existing `renderPeek`.
- **Verified**: headless 430px shot — Hub card opens a live peek of the real `orch` pane as clean flowing text (raw toggle intact); Nazim card correctly has no peek affordance. `payload.coordinators peekable=[True, False]`.

**Open decision for Nazim (flagged, not blocking):** Nazim's pane is cross-host (MacBook). Want cross-host peek too? That needs SSH `capture-pane` to the MacBook — fragile (MacBook asleep/off), and breaks panes.py's strict LOCAL+read-only contract. My call: **leave it local-only** (orch peekable, Nazim bus-activity) unless you want the SSH path — say the word.

## (c) Console consolidation (Studio vs Mini) — RECOMMENDATION (your call) 📐
Two instances drift: **STUDIO** `100.104.36.27:8787` (this checkout, `feat/war-room-live-feed`, fc-v10) = **the one the operator uses** (#7751, proven by his incognito test); **MINI** `100.83.21.34:8787` (`feat/operator-telegram-bridge`, fc-v8) = where you run. A fix on one doesn't reach the operator.

- **Canonical = Studio**, authoritative branch `feat/war-room-live-feed`. Non-negotiable (operator uses it).
- **Short term (recommend now):** put the Mini console on the SAME branch (`feat/war-room-live-feed`) so both serve identical code — kills "fix landed but operator can't see it" immediately. Cross-host restart = your/operator's to run.
- **Medium term:** the branch is **136 commits ahead of main** — merging to main is real work but the right end state (single source of truth; both machines deploy from main via one documented step so drift can't recur). Propose scheduling after this batch verifies.
- **Perf sub-item (#7753 task 2) — RESOLVED in this batch:** the coordinators fetch that was made **serial** (→1042ms, over the 500ms budget) is back in the **concurrent** pool (4th future on the warm connection). Measured warm payload **~155ms** (3 runs), well under budget. The tmux captures are cheap (5–20ms each); the cost was purely the serial 4th DB fetch.

## (d) Bulletproof PWA version-gate (#3640) — BUILT + verified ✅
The prior gate relied ENTIRELY on the SW `controllerchange` event — the exact unreliable iOS path that stranded the operator ("can't be deleting and re-adding for every update"). Added a **page-level gate independent of the SW**:
- **fleet.js** `checkVersion` (on load + every 60s): fetches `/api/version` (open, never SW-cached — sw.js skips `/api/*` — and cache-busted so even a wedged old SW can't feed a stale version). If the server is a **strictly-higher** `fc-vN` than the page's baked `APP_BUILD`, `hardResetForVersion` unregisters every SW + deletes every cache + reloads from network — bypassing the SW-update path.
- **Forward-only + loop-safe:** resets ONLY when server > page (never on equal, and never on server<page — the window after a static deploy but before the server restarts, when `/api/version` still reports the old baked build). Per-target `sessionStorage` stamp → at most one reset per version per session; if the network still serves stale after a reset it falls back to the amber badge instead of looping.
- **Re-synced the drift:** `APP_BUILD` was `fc-v7` while `sw.js` was `fc-v9` (a prior deploy bumped sw.js but not fleet.js, despite the "lockstep" comment). Both now `fc-v10`.
- **Verified**: 13/13 headless assertions incl. the safety-critical *server-behind→no-reset* and *loop-safe per-target stamp*.

**Scope honesty:** the gate protects every FUTURE update and self-heals a still-open PWA within 60s of a deploy. It cannot retroactively rescue a client already wedged on a PRE-gate SW (that old page has no gate) — that one needs the SW to update once (or one manual clear). From fc-v10 forward, no more stranding.

---

## Deploy notes (for whoever redeploys)
1. **Restart required** to activate (b) coordinator peek (backend sends `peekable`) + refresh `_BUILD_VERSION` to fc-v10. The static (fleet.js/sw.js/fc-v10 badge + gate) is already served from disk; the **pre-restart window is safe** (forward-only gate: server reports old < page fc-v10 → no reset; coord peek degrades to no-affordance against the old backend).
2. On restart, `/api/version` → fc-v10, badge matches, coordinator peek live.
3. Consider putting the **Mini** on `feat/war-room-live-feed` too (item c) so both stay in lockstep.
