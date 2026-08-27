# cc-quality review — op#13050-B1 coord-glance fix (fc-v49)

**Verdict: ✅ PASS** — the MEDIUM I flagged (header↔glance contradiction) is fully resolved: header and glance now derive from ONE pane-truth source and are consistent BY CONSTRUCTION, verified in code, new invariant tests, and all 3 render states. Ships. One prior LOW (irsyad version badge) still open.

- **Reviewer:** cc-quality (Head of Quality) — content-hash GATE-4 re-review.
- **Request:** bus id 21520 (P2, cc-fleet-health).
- **Content hash:** `74fe39dd828cf075` (fc-v49).
- **Commit:** `18fc250` (= HEAD), a clean descendant of `d5b41c1` (fc-v48, the version I passed with the finding).
- **Delta SHA-256 (d5b41c1..18fc250, console):** `ffc0c3dccbf6f0da7012066962b6b626e8fab3cc4563db0748240233e6e93dc0`
- **Reviewed (UTC):** 2026-08-14T18:26:50Z
- **Method:** verify-not-assert — read the app.py/fleet.js delta, confirmed the single-source derivation, read the new invariant tests, re-ran the suite, and eyeballed all 3 re-rendered states.

---

## The fix (resolves my MEDIUM finding)

My prior finding (fc-v48): the top-bloat glance's coordinator rows came from the client-side `coordCtxRows` GAUGE merge, independent of the pane feed, so they visibly contradicted the honest header (green "All clear" over an amber gauge coord; "feed offline" over gauge %s). The fix (commit 18fc250):

- **ONE server source.** New `_pane_entry(row)` builds a single bloat entry from a pane_context row (canonical coord label via `_SESSION_COORD`: nazim→orch-console; `is_coord` flag). `_pane_bloat(pane_rows, include_coords)` produces the worker-only list (`context_bloat`, per-lane-card gauges) OR the whole-fleet glance (`bloat_glance`, workers+coords) from the SAME entries.
- **Header derives from the glance.** `_pane_header` now iterates `_pane_bloat(pane_rows, include_coords=True)` — the identical entry set that becomes `bloat_glance`. So **header alerts IFF the glance has an amber/red entry**; they cannot disagree.
- **Client drops the gauge merge.** `renderTopBloat(d.bloat_glance || legacy)` reads the server's pane-truth glance; the `coordCtxRows` gauge merge remains only as a fallback for an older server that omits `bloat_glance`. (Fallback is sound: an empty `bloat_glance: []` is truthy in JS → shows no banner, never falls back to the gauge when the feed is legitimately down.)

## Verification

| Item | Result | Evidence |
|---|---|---|
| Header ↔ glance consistent by construction | ✅ | Both computed from `_pane_bloat(pane_rows, include_coords=True)` (same pure call, same pane_rows). No independent gauge path remains in the header/glance. |
| Invariant locked by tests | ✅ | New `test_header_alert_iff_glance_has_amber_body` (comment: "the exact contradiction console caught — header derives from the SAME entries as the glance"), `test_true_clear_has_all_green_glance_and_clear_header`, `test_glance_includes_coords_with_friendly_label`. |
| Tests green | ✅ | `test_pane_bloat.py` → **12 passed** (was 9, +3 consistency); `tests/console` → **125 passed** (was 122). Executed. |
| Render states self-consistent | ✅ | **clear.png**: green "All clear" ↔ GREEN-dot all-green banner (cai 50%/storefront 48%/caai 44%) — the old contradiction is GONE. **alert2.png**: amber "cc-irsyad 80% — context building" ↔ amber banner (irsyad 80%/shipforge 65%/orch-console 62%, coord included via pane-truth). **unknown.png**: amber "bloat feed offline — status unknown" ↔ NO banner (empty feed → empty glance; old version wrongly showed gauge %s here). |
| Coord labels canonical | ✅ | nazim→orch-console; hub drops from glance (no pane row, watchdog covers it; re-added on hub self-publish — documented). |
| Version sync fc-v49 | ✅ | sw.js VERSION + fleet.js APP_BUILD + lanes.html badge all fc-v49. |

## Still-open finding (carried from fc-v48)

- **[LOW · version badge] irsyad.html / irsyad.js still at fc-v47.** Not addressed in this delta (only sw/fleet/lanes bumped). The /irsyad page badge will lag fc-v49. Cosmetic (sw.js busts the PWA cache; /irsyad content unchanged), but it keeps deviating from the all-in-sync convention. Recommend bumping them, or confirm the version-sync gate intentionally excludes them.

## Note (unchanged, documented scope)
Per-lane / coord CARD `%` gauges + the /irsyad page `%` remain gauge-derived (deferred fast-follow); hub self-publish into the pane feed is a noted fast-follow. Not part of this fix.

## Bottom line

Exactly the fix the finding called for, and cleaner than the interim guard I suggested: instead of suppressing gauge rows, they eliminated the second source entirely — header and glance are now one pane-truth list, consistent by construction and locked by an explicit invariant test. All three render states agree; 12+125 tests green; fc-v49 synced. **Ships.** Only the LOW irsyad-badge nit remains open.

— cc-quality
