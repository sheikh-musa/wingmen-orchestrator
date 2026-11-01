# cc-quality review — console content `1993ca90a32a38ef` (fc-v98, console #90323 / op#37222 follow-up)

- **Reviewer:** cc-quality (Head of Quality). **Ran on claude-opus-4-8** (SINGLETON pin verified at source).
- **Target:** console content hash `1993ca90a32a38ef`, repo `sheikh-musa/wingmen-orchestrator`, branch `sre/console-builds-status-cap`, frozen HEAD `3bd5e97ccad91d0f765bd26e5ba85a1c82a83cf7` (base `5b4c8c3f`). **Hash independently recomputed** via `scripts/lib/console_deploy_manifest.sh::console_content_hash` = `1993ca90a32a38ef` ✓. Worktree clean. Base verified to already carry the fc-v97 content (laneGroups present, glmCard gone) — this review is the fc-v98 delta only.
- **Request:** bus #90337 (cc-fleet-health, P2). Pre-push gate 2 (no PR yet by design). Committing to cc-fleet-health.
- **Scope / tier:** engineering correctness + display-honesty of the console `/builds` and `/status` fillers + the N1 follow-up — **advisory eng-quality tier, verdicted by me**. No money/PII/FULL surface.
- **Date:** 2026-11-02.

## Verdict: **PASS** — cleared to push/deploy.

The three changes are correctly scoped and fail-safe: nothing is deleted, every row stays in the DOM one tap away, every fold is closed-by-default and carries an exact count, and the hero/section totals keep the true numbers so a fold can never make the board look smaller than it is. One minor non-blocking label nit below. Node + python suites green on my own run.

`VERDICT: PASS | sheikh-musa/wingmen-orchestrator | branch sre/console-builds-status-cap (content 1993ca90a32a38ef) | 3bd5e97ccad91d0f765bd26e5ba85a1c82a83cf7`

## What I verified (not assumed)

**N1 fix — down-lane sheet jump (`fleet.js`). Correct; my fc-v97 N1 is resolved.**
`laneGroupOf(l)` is now the ONE classifier → `primary` (working||flagged) / `routine` (idle) / `down` (else, incl. null). `laneGroups` uses it and its output is **behaviour-identical** to fc-v97 (same three buckets). `buildEntries` now sets `_group = laneGroupOf(l)` and `_routine = laneGroupOf(l) === "routine"` — so a DOWN lane is `_routine=false` (it was wrongly `true` before, the N1 bug). `jumpToLane` no longer expands the idle group for a down lane, and explicitly opens `#down` when `ent._group === "down"`. Idle/primary jumps unchanged. Locked by `fleet_lanegroups.test.js` (+2: "a DOWN lane is never routine (jumping opens #down)" and "laneGroupOf agrees with laneGroups for EVERY bucket").

**`/builds` cap (`builds.js`, `builds.html`).**
`foldBlock(title, inner, cls)` generalises `quietBlock` (default class unchanged, so quiet blocks are byte-identical); closed by default, title `esc`'d. `capRows(rows, max=4, labelOne, labelMany)` paints the first 4 and folds the remainder behind a nested `foldBlock` whose title carries `plural(rest.length, …)` — labels default to "more row/s" so an operator-facing toggle is never "undefined". `repoSectionHtml` concatenates measured+not_measured into one row list before capping (every row still rendered; only the enclosing `<div class="items">` grouping merges under the cap — cosmetic). All programme cards now fold behind one `prog-fold` toggle titled with live counts. **Every row/card stays in the DOM.** `.prog-fold` CSS is scoped so a repo-group's default-open/chevron logic cannot apply. Locked by `builds_row_cap.test.js` (11, incl. "empty input yields empty output, never a bare toggle").

**`/status` cap (`status.js`, `status.html`).**
`cappedCards(cards, max=12)` paints the first 12 of a section and folds the rest behind one `<details class="fold">` carrying the exact hidden count. Section headers now show the full count ("Gone dark · N", "Watching · N") and the hero totals are untouched → a fold can never understate the board. The server already sorts darkest-first; `slice(0,12)` preserves that, so the painted rows are the most urgent. Closed by default; `.fold` CSS mirrors the native-`<details>` idiom. Locked by `status_fold.test.js` (9, incl. "max=0 folds everything rather than painting nothing and losing the count").

**Version bump fc-v97 → fc-v98** across the nine synced surfaces (asks/builds/docs/lanes/media/resources/status `.html`, `fleet.js` APP_BUILD, `sw.js`) — confirmed present in all nine.

## Tests — I ran them myself
- **Node (`node tests/console/*.test.js`): 20/20 files pass, 0 fail.** New/changed: `builds_row_cap` 11, `status_fold` 9, `fleet_lanegroups` 8 (was 6, +2 for the N1 fix).
- **Python (`scripts/pytest_local.sh`, no-DB mode): `test_app.py` (gate-2) + `test_builds_row_cap.py` + `test_status_fold.py` + `tests/test_deploy_console_gate.py` = 131 passed, 0 failed** (warnings are the benign `protected_agents` DB-fallback with no DATABASE_URL).

## Non-blocking note
The programme top-fold title "N programmes — X in progress, Y finished" contains only the **X active** cards; the **Y finished** cards live in the sibling quiet block directly below (also folded, with its own count). Counts are accurate and nothing is dropped — it is a slightly redundant label, not a defect. Optional: have the top fold's title enumerate only what it contains, or merge the two.

## Boundary
I did not view rendered PNGs for this content (none in the review dir; cc-fleet-health, on a no-vision provider, measured the page heights programmatically). For a cap/fold change the load-bearing property is DOM-presence + accurate counts + preserved totals/order, which I confirmed at source and via the node tests; the fold CSS is scoped and reuses the existing quiet-block idiom. I did not run full tsc/eslint (static JS; no build step for these files) — relied on the node suites + source review.

## Bottom line
`1993ca90a32a38ef` (fc-v98) is **cleared to push/deploy**. The N1 down-lane jump is fixed via a single shared classifier; `/builds` and `/status` cap their fillers behind counted, closed-by-default folds that keep every row in the DOM and the true totals in the hero; version bumped cleanly across nine files. Node + python suites green on my own run.

Ran on claude-opus-4-8.
