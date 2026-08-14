# cc-quality review — console fc-v51 scroll/container fix

**Verdict: ✅ PASS** — a correct, minimal fix for the fc-v50 nested-scroller bug; root cause accurately diagnosed, addresses all three symptoms (PTR misfire / refresh snap-to-top / bottom clipping), no Approach-C design or functionality regression. Tests green, renders confirm. One persistent LOW (irsyad version, not part of this delta).

- **Reviewer:** cc-quality (Head of Quality) — content-hash GATE-4.
- **Request:** bus id 21711 (P2, orch-console). Focused delta on top of the already-passed fc-v50 (Approach C).
- **Content hash:** `8c1c8281814fbf8e` (fc-v51).
- **Commit:** `e9ee85b` (= HEAD), parent = the fc-v50 deploy record. Delta = 34 ins / 10 del: fleet.html (CSS), fleet.js (version constant only), lanes.html + sw.js (fc-v51 bump). **app.py untouched.**
- **static diff SHA-256:** `2197e126026441e5f65453d0aea0b9aed82349973a64d124d0890576092cc52b`
- **Reviewed (UTC):** 2026-08-14T22:32:24Z
- **Method:** verify-not-assert — read the full CSS/JS delta, reasoned through the overflow behavior, confirmed the dock is untouched, ran the suites, and eyeballed the scroll-bottom + sheet-open renders.

---

## Root cause + fix (both correct)

**Diagnosis is accurate.** Per the CSS overflow spec, when one axis is `hidden` the other axis' used value flips from `visible` to `auto`. So fc-v50's `body { overflow-x: hidden }` (with `body { height: 100% }`) silently made `<body>` a nested, viewport-capped scroll container — content scrolled INSIDE body while `window.scrollY` stayed 0. That produced exactly the three reported symptoms: (a) PTR's `scrollY > 0` guard never bailed → misfire; (b) the 8s refresh's `window.scrollTo` scroll-restore was a no-op → snap-to-top; (c) last sections clipped under the fixed dock.

**Fix is minimal and correct:**
- `overflow-x: hidden` moved to `<html>` ONLY — the legitimate document/viewport scroller (root-element overflow propagates to the viewport; `window.scrollY` / `document.scrollingElement === <html>` is authoritative). `<body>` no longer carries an overflow property → stays `visible` → not a scroller.
- `height: 100%` dropped from `html, body`; `body { min-height: 100vh }` added → body fills the viewport but grows with content in normal flow.
- `header.pulse` `position: sticky → relative` (z-index 20→1) → the monitoring strip scrolls in flow as the top of the ONE container (the operator's explicit ask: "merge the header with the page"). No sticky block stealing viewport or fighting PTR.
- `body { padding-top: env(safe-area-inset-top) }` added (header no longer sits at the inset), and the fixed `body::before` island-fill backdrop is kept (masks content scrolling up under the notch). The dock-clearance `padding-bottom: calc(inset-bottom + 78px)` is unchanged.

## Verification

| Item | Result |
|---|---|
| Single scroll container | ✅ overflow-x on `<html>` only; body has no overflow prop (not nested). Builder-confirmed `document.scrollingElement === <html>`. |
| Dock / fixed elements untouched | ✅ delta touches only `<html>`/`<body>`/`header.pulse`/`body::before`; the fixed dock + bottom sheet CSS unchanged. |
| No functionality regression | ✅ fleet.js delta is the version constant only (no logic). pytest tests/console/test_app.py = **53** (app.py untouched); node fleet_pace = **6**, fleet_topbloat = **3**. |
| Renders | ✅ **scroll-bottom**: page scrolls fully to the end — lane spine, "6 lanes" collapsed, Coordinators cards, "Your asks (4 open — 1 need you)", and the Full-lane-manager / irsyad nav buttons all in-view and clear of the resting dock; nothing clipped. **sheet-open**: the bottom action sheet raises correctly over scrolled content with the full Approach-C action grid (Peek + the NEW-badged stubs) — no regression. |
| Version sync fc-v51 | ✅ sw.js + fleet.js + lanes.html all fc-v51. |

## Design note (not a regression)
The header changing from sticky to scroll-away is the operator's explicit request ("everything in one container"), and the always-accessible action affordance remains the FIXED bottom dock (unchanged) + the tap-to-raise sheet — so no critical always-visible control is lost. The lower header background opacity + removed backdrop-blur are consistent with it now scrolling in flow rather than overlaying.

## Finding
- **[LOW · persistent] irsyad.html / irsyad.js at fc-v49** while the bundle is now fc-v51 (two behind). Not part of this delta (which touches only fleet/lanes/sw). Cosmetic (sw.js busts the PWA cache; /irsyad content unchanged), but the badge lag keeps growing. Recommend bumping irsyad in sync, or confirm the version-sync gate intentionally excludes it — I've now flagged it across fc-v48→v51.

## Bottom line
Correct root-cause fix, minimal surface, no regression: `<html>` is the single scroller, `window.scrollY` is authoritative again (PTR + scroll-restore work), and the page scrolls cleanly to the bottom with nothing clipped. 53 + 6 + 3 tests green; scroll-bottom + sheet-open renders confirm. **Ships.** Only the standing irsyad-badge LOW remains.

— cc-quality
