# Console fix — LANE PEEK renders as flowing text, not per-line boxed shards

**Agent:** cc-console · **Branch:** `feat/war-room-live-feed` · **Commit:** `cd9517e` · **Build:** fc-v7
**Date:** 2026-07-12 · **Handoff:** cc-uiux to verify before operator redeploys. Console NOT restarted (operator owns deploy).

## Problem (operator-flagged)
The feed-mode live peek rendered each cleaned tmux pane line as its own **bordered row** (`.peek .row`, `border-bottom`) prefixed with a `·`/`▶` glyph. A pane line long enough to wrap therefore broke into choppy boxed fragments — a lane's output read as broken mid-sentence shards instead of flowing text.

## Fix
Render the cleaned pane as continuous, flowing text — one **borderless block per pane line** that wraps as a whole paragraph. Dropped the dot/box chrome; kept a subtle inset accent bar on the current (last) line so the live tail is still spottable. Raw toggle unchanged; `panes.py` capture/clean logic untouched (this was purely a render/CSS problem).

| File | Change |
|---|---|
| `static/fleet.js` | `renderPeek` feed branch: `.row/.g/.tx` glyph rows → single `<div class="ln">` per line. `APP_BUILD` fc-v6→v7. |
| `static/fleet.html` | Replaced `.peek .row*` rules with `.peek .ln { white-space:pre-wrap; overflow-wrap:anywhere; }` + `.ln.now` accent bar. `.body` gets `padding:8px 0`. |
| `static/sw.js` | `VERSION` fc-v6→v7 (cache reset + reload-on-update). |

## Verification (headless Chrome, 430px frame)
- Screenshot: `reports/uiux/peek-flowing-text-430px-20260712.png` — multi-line peek reads as natural wrapping paragraphs; long tokens (`GOUMLYNE_DATABASE_URL`, `goumlynecruxrlmzlntp`) wrap cleanly; `raw ⌄` toggle present top-right.
- Measured no horizontal overflow: line `scrollWidth` (440) == body content width (440) — text wraps to container, does not spill.
- Confirmed no stale references to the removed `.row/.g/.tx` peek classes anywhere in fleet.js/fleet.html.

## Notes for cc-uiux
- Please eyeball both **feed** and **raw** modes at 430px on a real capture (my harness used representative sample text).
- The `.ln.now` accent is `box-shadow:inset 3px 0 0 var(--accent)` on the last line — verify it reads as "current" without looking like a selected/boxed row (the thing we just removed).
- Scroll-position preservation across the 3s self-refresh is unchanged (still restores `.body` scrollTop).
