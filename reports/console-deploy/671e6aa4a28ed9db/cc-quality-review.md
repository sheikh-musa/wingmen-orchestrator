# cc-quality review — console fc-v41 (invisible Lane-manager button fix)

**Verdict: ✅ PASS** (1 LOW non-blocking finding — dead CSS; does not gate the ship)

- **Reviewer:** cc-quality (Head of Quality)
- **Request:** bus id 20174 (op#12475), P2
- **Scope:** `git diff` since fc-v40 (HEAD = 468143d) — fleet.html, fleet.js, sw.js, lanes.html
- **Branch:** `fable/substrate-safe-fixes`
- **Diff SHA-256:** `2458c02b26bb70b58b118c1769f7ac91433ae083f5284b1f9187d30efc3fba7c`
- **Reviewed (UTC):** 2026-08-13T11:57:03Z
- **Method:** verify-not-assert — read the full diff, traced the pulse DOM structure, grepped for dangling `#strip`/orphan CSS, `node --check` on fleet.js, eyeballed the iPhone-13 render.

---

## What changed (confirmed against source)

1. **Status chips removed** — the `#strip` render block deleted from `fleet.js` (the `$("strip").innerHTML = …` working/idle/flagged/offline chips), and the `<div id="strip" class="strip">` container removed from `fleet.html`. Replaced in JS with a defensive comment (no `$("strip")` call remains).
2. **`.laneslink` button moved INSIDE the sticky `.pulse`** — was a standalone block after the pulse closed; now sits inside `#pulse` (fleet.html L407, pulse block L393–408), right after `#topBloat`. Margin adjusted `14px 16px 6px` → `12px 0 2px` (horizontal margin zeroed because the pulse already supplies 18px side padding), radius 14→12, padding 16→15px.
3. **fc-v40 → fc-v41 sync** — sw.js `VERSION`, fleet.js `APP_BUILD`, lanes.html badge. sw.js + lanes.html diffs are the version bump ONLY.
4. (Render tooling) gate now renders via Playwright iPhone-13 emulation (`render_console_playwright.py`) — the attached `fleet.png` is that profile.

## The fix is sound

- `.pulse` is `position: sticky; top: env(safe-area-inset-top); z-index: 5` (fleet.html L82). Placing `.laneslink` inside it means the button is **pinned with the header and can never scroll under it or be covered** — exactly the op#12475 ask ("always visible + uncoverable"). Confirmed the `<a class="laneslink">` is a direct child of `#pulse`, closed by the pulse's own `</div>`.

## Requested checks

| Check | Result | Evidence |
|---|---|---|
| No dangling `#strip` reference | ✅ | `grep '"strip"'` in fleet.js → only a comment at L577; no `$("strip")` / `getElementById("strip")` remains. `#strip` gone from fleet.html. |
| Button-in-pulse layout | ✅ | `#pulse` (sticky, L82) contains `.laneslink` at L407; render shows it inside the sticky band, above the section divider. |
| fleet.js integrity | ✅ | `node --check nervous_system/console/static/fleet.js` → clean. |
| Preserved functionality | ✅ | Diff touches only the strip removal + button move + version; Needs-you, Coordinators (collapsed ▸), Your-asks (collapsed ▸), Lanes list, per-lane peek/reset/switch, fleetSwitch, pull-to-refresh, SW auto-update, safe-area fix all untouched. |

## Render eyeball (iPhone-13 profile)

`fleet.png`: fc-v41 badge; **status chips gone** (no working/idle/flagged/offline strip); "🔑 Lane manager →" button prominent and inside the sticky pulse; below it Needs-you → Coordinators (`▸` "hub · console · cai") → Your-asks (`▸` "15 open") → Lanes list + lane cards render correctly. Matches intent — the invisible-button bug is fixed.

## Findings

- **[LOW · non-blocking · consistency]** Orphaned CSS in `fleet.html`: `.strip` (L150) and `.chip` (L151, L374) are now dead — no `class="strip"`/`class="chip"` is emitted anywhere in fleet.js/fleet.html after the status-chip removal. This adds to the fc-v40 orphans (`.qlane/.qlane-h/.qrow/.qtitle/.qchip` ~L101–116). **Recommend one dead-CSS sweep** folding fc-v40 + fc-v41 orphans together. No effect on function or render; not a ship blocker.

## Observation (not a finding)

- The sticky `#pulse` is now taller (brand row + "All clear" + top-bloat + button all pinned). On a small viewport the pinned header occupies more vertical space when scrolled. This is inherent to the "always-visible/uncoverable" ask and is the correct implementation of it — noted for awareness, not a defect.

## Bottom line

Small, clean, self-contained fix. No dangling refs, JS parses, the button is genuinely uncoverable (sticky pulse), no regressions, render matches intent. **Ships.** One cosmetic dead-CSS finding — fold into a follow-up sweep with the fc-v40 orphans.

— cc-quality
