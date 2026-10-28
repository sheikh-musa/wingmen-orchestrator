# cc-quality review — console content `0c41aafa048cc14b` (fc-v88, op#35074)

- **Reviewer:** cc-quality (Head of Quality). Ran on claude-opus-4-8.
- **Target:** console content hash `0c41aafa048cc14b` in worktree `orchestrator.wt-console-revamp` (branch `lane/console-revamp`, HEAD `1202a15` + the uncommitted fc-v88 working tree). **Hash independently recomputed** via `scripts/lib/console_deploy_manifest.sh::console_content_hash` = `0c41aafa048cc14b` ✓ (matches the request).
- **Request:** bus #79225 (cc-console-revamp-1). Committing to fleet-health (their #79177; ruled orch-console #79176).
- **Scope:** engineering correctness of a deliberately small, display-only change. No aesthetic eyeball owed to me (orch-console owns that per 77019).
- **Date:** 2026-10-28.

## Verdict: CODE **PASS** — cleared to deploy. The change is correctly scoped to offline key-chip suppression plus the fc-v87→fc-v88 version bump; every "untouched" claim holds at source; and the full console suite is green on my own run (node + python). Aesthetic sign-off and push remain orch-console's / the lane's.

## What I verified (not assumed)

**The one behavioral change (fleet.js `tileHtml`).** `tok` becomes `""` when `l.bucket === "offline"`, else the original `tokChip(l.auth_fp, pool, l.auth_mismatch)` verbatim. So an offline tile paints no key chip; a live tile is byte-identical to fc-v87 (the else branch is the untouched control). `tok` has exactly one consumer — the `pills` line (`(tok || mdl) ? …`) — so suppressing it removes only the key chip; the model chip still renders if present.

**The "untouched" claims — all literally true at source:**
- **`data-pool` attr** (line 709) is emitted from `pool` = `poolOf(l)`, which is still computed unchanged. Offline tiles keep their `data-pool`.
- **fc-v63 roll-up** is computed by `poolRollup(lanes, lastCoords, poolFilter)` from the **lane data model**, not by scanning rendered chips — so an offline lane still counts by key **assignment** even with no painted chip (exactly the assignment-vs-liveness split the comment and op#35074 intend). Locked by `fleet_keyroll.test.js` ("poolRollup counts EVERY lane with a known key … ASSIGNMENT, not liveness").
- **Model pill** — `mdl = mdlChip(l.model, l.model_src)` is unchanged.
- **Pool-filter dimming** — `off = poolFilter && (… pool …)` uses `pool` + `l.auth_fp`, never `tok`, so an offline tile still dims/lights correctly under a tapped key-pool.

**Version bump fc-v87 → fc-v88 across the 8 surfaces.** `sw.js` `VERSION`, `fleet.js` `APP_BUILD`, and six html `#build` badges (asks, builds, docs, lanes, media, status). I diffed those seven files: **version-string-only, no logic** (8 insertions / 8 deletions, all one-line swaps).

**No API / backend / data change.** The content manifest's only modified files are `static/*.html`, `static/fleet.js`, `static/sw.js` (plus the node test, which is outside the console package). No `nervous_system/console/**.py` and no Dockerfile changed — display-only, as the ask states.

## Tests — I ran them myself (the standing discipline: the deploy gate's Gate-2 runs only `test_app.py`)

- **Node (all 13 console suites, `node tests/console/<f>.test.js`): GREEN.** `fleet_keyroll.test.js` = **12 passed** (the new TDD lock); every sibling fleet suite passes — `fleet_actions` 7, `fleet_glm` 16, `fleet_governance` all, `fleet_homebar` 2, `fleet_lanectx` 12, `fleet_pace` 15, `fleet_topbloat` 3 — plus `asks_board`, `builds_view`, `docs_media`, `status_board`, `irsyad_lanectx`. All load `fleet.js`; none regressed.
- **Python full `tests/console/` (no-DB sanctioned mode): 459 passed, 0 failed** (warnings are the benign `protected_agents` DB-read fallback with no DATABASE_URL). I ran the whole directory, not just `test_app.py`.

## Note (non-blocking)
The "no key badge when offline" vs "still counted in the roll-up by assignment" split is a deliberate, orch-console-ruled display decision (#79176) — it is internally consistent (liveness = the painted chip; assignment = `data-pool`/roll-up) and I verified the code implements exactly that. If an operator later finds the hidden-chip-but-still-lit-under-filter behavior confusing, that is a product call for orch-console, not a code defect.

## Bottom line
`0c41aafa048cc14b` is **cleared to deploy**. Offline tiles stop painting a stale key chip; data-pool, the fc-v63 roll-up, the model pill, pool-filter dimming, and all live tiles are untouched (verified at source); the version is bumped cleanly across all 8 surfaces; and node + python console suites are fully green on my own run.

Ran on claude-opus-4-8.
