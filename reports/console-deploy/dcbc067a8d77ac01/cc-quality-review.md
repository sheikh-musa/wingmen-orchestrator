# cc-quality console deploy-gate review — content hash `dcbc067a8d77ac01`

**VERDICT: PASS** — clear to deploy (fc-v57, item-2 SRE-activity-per-lane display, Nazim-approved design #31768 / op#31750). Two LOW findings below are advisory follow-ups; neither blocks.
**Reviewer:** cc-quality (opus-4-8) · **Date:** 2026-08-23 · **Gate:** deploy_console.sh GATE 4 (op#12457), requested by cc-fleet-health #31772.
**Scope reviewed:** working-tree diff of `nervous_system/console/app.py` (+44) + `static/{fleet.html,fleet.js,lanes.html,sw.js}`; renders `fleet.png` + `lanes.png`; console test suites (re-run at source).

## Design / UX soundness — SOUND
Each bloated lane now shows the SRE's live disposition next to ctx% (4 states: healthy / SRE watching / SRE recycling / SRE holding), and the resting banner reads e.g. "cc-quality 64% — SRE watching" instead of a bare "context building". This directly closes the "am I SRE?" gap (op#31750): a red/amber lane now always shows *who's on it*, so the operator sees it's handled and can look away. Good operator-facing improvement; the resting view stays clean (pills live behind the BLOAT-cell tap per the fc-v52 design; banner is the always-on surface).

## Derive-not-store (staleness-safety) — EXCELLENT, endorsed
`_sre_disposition(pct, idle_verdict)` is DERIVED live on every render from pct + the body-oracle idle_verdict — there is **no stored disposition field** that could go stale and render a lie. This is exactly the right choice: it structurally avoids the stale-stored-field failure mode (cf. invariant_registry rotting to 85% NULL). Strongly endorse.

## Correctness (verified at source)
- Thresholds align with the live bars: `watching` at `_CTX_SOFT` (0.60 = amber start), `recycling|held` at `_SRE_RECYCLE_PCT=65` which equals the real recycle bar `PANE_FIRE_K=650`(k)/1M window (scripts/auto_recycle_on_bloat.py:58). `recycling` iff `idle_verdict=="IDLE_EMPTY"` (a real body_activity_oracle verdict; the daemon Stage-acts on exactly that), else `held`.
- **Fail-safe direction correct:** an unreadable pct → `watching` (amber), never a false `healthy` — surfaces uncertainty rather than hiding it.
- Wiring: `_pane_entry` carries `sre_disposition`; `_pane_header` propagates the worst lane's; fleet.js renders it in the banner (with a `|| "context building"` fallback) and as a per-lane pill (guarded `d && d.label` so coord glance rows without a disposition don't break). `esc()` applied to state+label (XSS-safe even though both come from fixed enums/maps).
- CSS: `.topbloat .sre` uses `currentColor` + theme vars (`--good`/`--warn`/`--dim`); `healthy → display:none`. Not orphaned (all four state classes are emitted by fleet.js).
- Version **fc-v57** consistent across sw.js / fleet.js / lanes.html.

## Accessibility / theme — OK
Disposition is conveyed by **both** color and a text label ("SRE recycling", etc.) → colorblind-safe (meaning survives without color). Pill is theme-aware (CSS vars + currentColor border). Font 700 10.5px is small but consistent with the dense iPhone-13 console layout.

## Renders (eyeballed) — CLEAN
`fleet.png`: renders clean at fc-v57; banner shows the OLD "cc-quality 64% — context building" (truncated) — **EXPECTED pre-deploy** (backend still old); post-deploy it becomes "…— SRE watching" (64% is amber, <65%). `lanes.png`: clean, fc-v57, no breakage (version-bump only on that page). No layout breakage on either.

## Tests — GREEN (re-run at source, not taken on trust)
`.venv/bin/python -m pytest tests/console/test_app.py` → **85 passed, 0 failed**; `tests/console/test_pane_bloat.py` → **23 passed** incl. the 6 new `_sre_disposition` TDD cases (healthy / watching / recycling-when-idle / held-when-active / header-worst-carries / cliff-idle). Total 108 python green, 0 failures. (Dispatch said "110" — a minor miscount; all green regardless.)

## Findings — LOW, non-blocking (advisory follow-ups)
1. **Coupling is comment-only, not test-enforced.** `_SRE_RECYCLE_PCT=65` is tied to `PANE_FIRE_K=650` by a comment ("if that bar moves, move this with it") with no test. Correct at rest, but if the recycle daemon's bar changes, the console would silently show a disposition at a threshold the daemon no longer uses. Recommend a coupling test (assert `_SRE_RECYCLE_PCT == PANE_FIRE_K * 100 // 1000`) or derive the const. Display-only. (NB: auto_recycle_on_bloat.py has its OWN stale comments calling 650k "~85% of 1M" at lines 382/440 — pre-existing daemon-comment drift, 650k is 65%; not this PR's scope but worth a cleanup.)
2. **"SRE holding (active work)" label is imprecise for a wedged lane.** `held` fires for any ≥65% lane whose verdict isn't IDLE_EMPTY — including GHOST_WEDGED/UNSURE. A wedged lane isn't "active work" (it's stuck; a separate watchdog recovers it). The not-recycling classification is technically correct, but the "(active work)" parenthetical could mislabel a wedged lane. Consider "SRE holding" without the claim, or a distinct wedged state. Edge-case display nuance.
- *Pre-existing (tracked, not this PR):* orphaned CSS in fleet.html (`.qlane/.qchip/.strip/.chip`) still unswept — flag for a future console sweep.

**Bottom line: PASS. Deploy fc-v57.** The disposition display is well-designed (derive-not-store, colorblind-safe label+color, solves the am-I-SRE gap), thresholds are correctly aligned to the live recycle bar, the fail-safe direction is right, and it's well-tested (108 green incl. 6 new TDD) and renders clean. The two LOW items are advisory follow-ups, not deploy-blockers.
