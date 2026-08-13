# cc-quality review — console fc-v40 dashboard de-clutter

**Verdict: ✅ PASS** (1 LOW non-blocking finding — dead CSS; does not gate the ship)

- **Reviewer:** cc-quality (Head of Quality)
- **Request:** bus id 20147 (op#12294 / op#12455), P2 — first dogfood of the substrate-wide ship-gate
- **Scope:** `git diff nervous_system/console/static/` (fleet.html, fleet.js, sw.js, lanes.html)
- **Branch:** `fable/substrate-safe-fixes`
- **Diff SHA-256:** `bf3d3d1290b740fd3cf0deaa0adccd7b0dfbd980123102f8229b5dc5d6fb1f21`
- **Reviewed (UTC):** 2026-08-13T10:56:21Z
- **Method:** verify-not-assert — read the full diff, grepped for dangling refs, `node --check` on fleet.js, traced the collapse-default wiring end-to-end, and eyeballed both renders.

---

## What changed (confirmed against source, not just the changelog)

1. **`laneslink` promoted to a primary nav button** (fleet.html CSS) — flex-centered, indigo-lit gradient fill, clear border, full-height tap target, section-gutter margins. The later media-query `.laneslink` override was trimmed to defer radius/padding to the base rule (consistent, no conflict).
2. **Coordinators moved above "Your asks"** and **default-collapsed** — HTML `#coordChevron` initialized to `▸`, `coordExpanded = false` (was `true`).
3. **"Your asks" backlog default-collapsed** — `backlogExpanded = false` (was `true`).
4. **"Lane worklists" section removed** — `#laneQueues` markup, `renderQueue()`, `queueChip()`, and `STALE_ACTIVE_S` all deleted; the `renderQueue(d.queue)` call is gone from `applyData`. `/api/fleet` untouched; `d.queue` still ships in the payload, just no longer consumed.
5. **fc-v39 → fc-v40 sync** — sw.js `VERSION` and fleet.js `APP_BUILD`, plus lanes.html build badge. sw.js and lanes.html diffs are the version bump ONLY.

## Regression checks — preserved functionality (all intact)

| Item | Result | Evidence |
|---|---|---|
| Backlog swipe | ✅ | `bindBacklogSwipe()` still called at end of `renderBacklog` |
| Coordinator peek | ✅ | `bindPeeks/bindResets/bindSwitches` still called in `renderCoordinators` |
| Lanes list + fleetSwitch + per-lane peek/reset/switch | ✅ | `renderLanes`, `#fleetSwitch` handlers, bind* all present; untouched by diff |
| Pull-to-refresh | ✅ | untouched |
| SW auto-update | ✅ | sw.js update logic untouched; version-only bump |
| fc-v39 header safe-area fix | ✅ | `env(safe-area-inset-top)` rules present in fleet.html, not touched |
| No dangling refs (`renderQueue`/`queueChip`/`laneQueues`/`d.queue`/`STALE_ACTIVE_S`) | ✅ | grep finds them **in comments only** |
| JS integrity after the ~54-line deletion | ✅ | `node --check nervous_system/console/static/fleet.js` → clean (no dangling brace) |

## Collapse-default wiring (traced end-to-end)

- **Backlog:** "Your asks" has no static HTML chevron — the affordance is injected by `renderBacklog`, which renders a `#blToggle` header (`▸` collapsed / `▾` expanded) + "N open — K need you" summary + swipe hint, with `#blList` set `display:none` when collapsed and a click handler that flips `backlogExpanded`, toggles the list, and updates the chevron. **Correct — a collapsed backlog is expandable.**
- **Coordinators:** `#coordHead.onclick` (deliberately `onclick`, not `addEventListener`, so per-tick re-renders don't stack listeners) toggles `coordExpanded`, `#coordinators` display, and `#coordChevron`. Initial `▸`/`display:none` match `coordExpanded=false`. **Correct.**
- **Attention safety:** the separate **"Needs you"** section (`#needs`) sits above the collapsed backlog, so operator gate-asks still surface even with "Your asks" collapsed — the de-clutter does not bury urgent items. `#backlogCount` ("N need you") also still renders in the section header.

## Render eyeball

- **fleet.png:** fc-v40 badge; prominent "🔑 Lane manager →" button; order Needs-you → Coordinators (`▸`, "hub · console · cai") → Your asks (`▸`, "15 open", swipe hint) → Lanes; **no "Lane worklists" section** after the lanes list. Matches intent.
- **lanes.png:** fc-v40 badge; lane manager renders fully (switch group/ALL, token/model apply, all lane rows). Unchanged beyond version bump.

## Findings

- **[LOW · non-blocking · consistency]** Orphaned CSS in `fleet.html` (~lines 101–116): `.qlane`, `.qlane-h`, `.qlane-h .qn`, `.qrow`, `.qtitle`, `.qchip` (+ `.queued/.stale/.work/.wait/.over`) style the now-deleted `#laneQueues` rows and are dead. No effect on function or render. Recommend removing in a follow-up sweep (mildly ironic to leave dead rules in a "de-clutter"). Not a ship blocker.

## Bottom line

Functionally clean, no regressions in any preserved surface, collapse-default wiring correct on both sections, renders match intent, JS parses. **Ships.** The one finding is cosmetic dead CSS — fold it into a later cleanup.

— cc-quality
