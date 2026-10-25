# cc-quality review — fc-v84 docs/media Calm Command consistency pass (bus #76345, op#33598 #586)

- **Content hash:** `2a29d45623bee55b` (recomputed at source via `console_content_hash` on the worktree at HEAD = `2a29d45623bee55b` ✓).
- **Branch:** `lane/console-revamp` @ `65d1609` (worktree `orchestrator.wt-console-revamp`). Two commits on fc-v83: `87935cc` (docs.js/media.js deep-link + esc hardening) + `65d1609` (HTML restyle + GATE-1 widen + badges).
- **Base:** `57a79693…` (my fc-v83 head). Files: docs.html/media.html (restyle), docs.js/media.js (+deep-link +esc), asks.html/fleet.js/lanes.html/sw.js (version bump only), scripts/deploy_console.sh (GATE-1 4→6), tests/console/docs_media.test.js (new), reports/console-deploy/2a29d45623bee55b/render-harness.py (new, under reports/ — NOT in the gated set). **Zero gated `*.py` changed.**
- **Reviewer:** cc-quality (ran on claude-opus-4-8) · 2026-10-26
- **Verdict:** **PASS — clear for deploy_console Gate-4.** The two security-relevant JS deltas are exactly as specced and safe; the deep-link is byte-identical to the asks.js pattern I already vetted; esc hardening is correct; docs/media read as part of the Calm Command system. No blockers; 2 minor mobile-only aesthetic nits + 1 pre-existing out-of-scope note.

INDEPENDENT pass verified at source on the PR head, not a sign-off on the lane's green.

## JS deltas — exactly two per file, both safe (grep/diff confirmed)
Over `docs.js` and `media.js`, the ONLY changes are:
1. **The `#t=` fragment deep-link — byte-identical to the asks.js fc-v83 pattern I cleared.** Same `tokenFromHash` with `/^#t=([^#&\s]{8,})$/` (reads `location.hash`, not a query param), persist-to-localStorage **before** the token is read, then `history.replaceState(null, "", location.pathname + location.search)` strip, 8-char floor matching the real `CONSOLE_TOKEN`. No drift from the vetted copy — so all the fragment-only leak-safety properties (no server log / preview / referrer, no lingering URL) carry over unchanged. The node test's **cross-page parity** assert corroborates the identity.
2. **`esc()` gains `.replace(/"/g, "&quot;")`** — real XSS hardening: both pages interpolate server-derived strings into **double-quoted HTML attributes** (`data-search="…"`, `data-url="…"`, `data-proj="…"`, `alt="…"`), where a raw `"` could previously break out and inject an event handler. Before this diff esc escaped only `& < >`; now it also escapes `"`, closing the attribute-breakout vector. All attribute sites use double quotes (not single), so `&quot;` is the correct and complete escape for the contexts used.
- A node-only `module.exports = { tokenFromHash, esc }` is appended (guarded by `typeof module !== "undefined"`, inert in the browser) for the test. Nothing else in the JS moved.

## XSS / URL safety — verified at source
- **All attribute interpolations are `esc()`'d** (now `&quot;`-safe). **All URLs are `encodeURIComponent`'d**, not esc-reliant: docs `href`/fetch = `"/docs/"|"/api/docs/" + encodeURIComponent(repo) + path.split("/").map(encodeURIComponent).join("/")`; media `fileUrl` = `"/api/media-file/" + encodeURIComponent(...)`. So URLs are always same-origin, prefixed, and cannot break out of the attribute or carry a `javascript:` scheme.
- **Media renders only blob URLs.** `fetchBlobURL` fetches same-origin (auth header) and returns `URL.createObjectURL(blob)`; the lightbox `<iframe src>`/`<img src>` and "Open raw" use that blob URL — not a server string — so no injection via media src. `alt` is esc'd.
- **Pre-existing, OUT OF SCOPE (not this diff, not a blocker):** `docs.js` renders the doc BODY as raw HTML — `'<article class="article">' + doc.html + '</article>'` (unescaped). This is the docs viewer's existing design: `doc.html` is the server-side-rendered markdown of **first-party repo docs** (trusted content). fc-v84 does not touch this path. Defense-in-depth note for orch-console's awareness only: this relies on the server's markdown renderer not emitting active content; if a doc could ever carry untrusted markdown, that sink would need sanitizing. No action for this ship.

## Gate + version-sync
- **GATE-1 correctly widened 4→6:** `deploy_console.sh` now extracts `docs.html` (`DB`) + `media.html` (`MB`) badges and fails unless `sw==fleet==lanes==asks==docs==media`. Verified **all six surfaces = fc-v84** → GATE-1 will pass. (The `[1/4]` label is the gate number, not the surface count — correct.)
- Zero gated `*.py` changed → the console backend is byte-identical to the fc-v83 I cleared; `render-harness.py` is under `reports/` (local stub API, no prod touch), outside the content-hash set.

## Empirical (my own, at 65d1609)
- Content hash recomputed = `2a29d45623bee55b` ✓.
- **node `docs_media.test.js` → OK** (docs.js helpers, media.js helpers, **cross-page parity**, tokenFromHash flow).
- **Aesthetic eyeball (mine per the gate — lane is GLM/image-blocked):** all 8 renders are true-dimension (390×3 = 1170px mobile; 1440 desktop). docs (catalog/article/strip) + media (catalog/lightbox/strip) now share the Calm Command chrome/cards/hairlines/mono-meta/green-accent/token-pill — consistent with lanes/asks. The markdown article renders correctly (H1/H2/list/code/blockquote); the media two-column thumbnail grid + lightbox (Back / Open raw, centered image over dimmed catalog) work; no overflow, tap targets adequate at 390px; desktop header lays out cleanly.

## Minor / non-blocking
1. **media mobile header:** at 390px the `fc-v84` pill wraps to its own row because the title carries an extra `Docs` nav link (docs/asks keep the pill on the title row). Readable, no cut-off; desktop is unaffected. Cosmetic.
2. **media lightbox breadcrumb** wraps to two lines on 390px and sits a little tight against Back / Open raw. Readable, no overflow. Cosmetic.

**Bottom line:** PASS — the deep-link is the vetted asks.js pattern unchanged, the esc `&quot;` hardening is correct and complete for the double-quoted-attribute contexts used, URLs are encodeURIComponent-safe, media shows only blob URLs, GATE-1 is correctly widened to six, and the pages read as one system. Clear for Gate-4 at content hash `2a29d45623bee55b`.
