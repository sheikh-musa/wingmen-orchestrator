# cc-quality review — item-4b: deploy_console gate hashes the console BACKEND (5a787f1)

**Reviewer:** cc-quality (Opus 4.8, `.quality_model`=claude-opus-4-8 confirmed at source)
**Requested by:** cc-fleet-health (bus #31853)
**Target:** commit `5a787f1` on `fable/substrate-safe-fixes` (= current HEAD). 3 files, +255. Gate/recovery-critical.
**Scope of commit:** close the BACKEND blindspot — the op#12457 review-gate content-hash covered only the 5 static frontend files, so a backend-only change (the console runs as `python -m nervous_system.console`) shipped UNREVIEWED.
**Verdict:** ✅ **PASS for item-4b's stated scope** (backend blindspot closed, correctly, mutation-verified) — **with one MEDIUM finding: a twin static-side blindspot the new boundary doc now obscures.** Safe to land; the finding is a fast-follow + a doc-honesty fix that the commit's own #31843 principle requires.

## The five challenges — verified at source + execution
- **A — DEAD-MAN'S-SWITCH: does a backend-only edit really move the hash?** ✅ **Mutation-proved.** I neutered the manifest's backend `find` line (static-only) → `test_backend_only_edit_changes_the_hash` + 4 siblings (`nested_backend`, `new_backend`, `real_console_backend`, `includes_recursive_backend`) went **RED**. Restored (0 diff). The switch genuinely bites; not vacuous. 12/12 gate tests green at HEAD.
- **B — recursion + `__pycache__`.** ✅ `find nervous_system/console -type f -name '*.py' -not -path '*/__pycache__/*'` is recursive; the test tree's nested `hosted/view.py` is gated and the `.pyc` is excluded; `test_real_console_backend_is_covered` confirms app.py/db.py/auth.py/panes.py/hosted_view.py on the real tree.
- **C — sourcing under real invocation.** ✅ Ran the real `bash scripts/deploy_console.sh`: printed `== deploy_console gate (content 510d5c8d25586ca3) ==` (seam sourced, hash computed, no false "manifest seam missing"). The header hash **matches** my independent `console_content_hash` → the gate uses the seam, not a stale parallel copy. `dirname "${BASH_SOURCE[0]}"` resolves correctly after the internal `cd` (reproduced). Seam-missing path fails loud (exit 2) — fail-safe, never a silent bad hash.
- **D — hash portability/stability.** ✅ `510d5c8d25586ca3` stable across re-runs, and **identical** when the exact gated fileset is copied into a different temp dir (relative paths → content-not-location dependent). No false drift.
- **E — coverage boundary / shared imports.** ✅ Grep of the whole console package: the only import reaching outside `nervous_system/console` is `scripts.lib.lane_token_resolver` (a fleet-wide token util, not console-serving logic). **Concur** leaving it out per Nazim.

Bonus signal: the gate correctly **REFUSED** at GATE 2 (`console tests FAILED`) on the uncommitted item-4a app.py red test — the gate working as intended (that RED is item-4a, not in 5a787f1).

## FINDING [MEDIUM] — the static coverage is still partial, and the new boundary doc obscures it
The gate hashes **5 of ~13 SERVED static files.** `app.py::_serve_static` demonstrably serves, by route: `index.html` (`/`, `/index.html`), `irsyad.html`+`irsyad.js` (`/irsyad`, `/static/*`), `docs.html/js`, `media.html/js`, **`app.js` (26KB — core SPA logic)**, `manifest.json`, and `icons/*` — **none gated.** A change to any of them ships **UNREVIEWED** — the *same* unreviewed-ship class item-4b closes for the backend.

The seam header + deploy_console.sh both now say the hash "covers the console **PACKAGE** (`nervous_system/console/**`)". That materially **overstates** coverage: it covers all `*.py` + only 5 static files. By Nazim #31843's own stated principle — *"a KNOWN, WRITTEN boundary is fine; a silent one is what we're killing"* — this static gap is currently **silent/obscured**, which is precisely the anti-pattern the item is killing.

- **Not a regression** from 5a787f1 (op#12457 also gated only these 5). But 5a787f1 re-touched and re-documented this exact list, so the doc-accuracy defect lands here.
- **Recommendation:** fold the served static assets into the manifest the same way the backend was — e.g. `find nervous_system/console/static -type f` for served types (`.html/.js/.json/.css/.ico/.png`), sorted + LC_ALL=C, mirroring the `*.py` block. Cheap; closes the twin blindspot. If a 5-file cut is deliberate, the doc must name **which** static files are cut and **why** (not imply package-complete), and the widen is a decision to **escalate** to Nazim (who owns the console + set the boundary) — same discipline you applied to the backend widen. Advisory: I flag; I don't adjudicate the widen.

## FINDING [LOW] — hash construction: no per-file delimiter between contents
Contents are `cat`'d back-to-back with only a single separator between the manifest block and the content block. A content **move across an adjacent-file boundary** (bytes from the top of one module to the bottom of the alphabetically-preceding one) leaves both the manifest text and the concatenation identical → hash collision → missed change. The manifest-first design already catches add/remove/rename and any length-changing single-file edit, so this needs a contrived compensating move — unrealistic for real code, but this is a gate-integrity path. **Cheap hardening:** interpose the relative path (or a NUL + byte-length) before each file's content in the digest.

## NOTE [LOW]
`nervous_system/console/Dockerfile` (build/run definition of the deployed artifact) is ungated — arguably deploy-provenance rather than content-hash, but worth a conscious placement.

## Escalation
None adjudicated by me. The static-widen (MEDIUM) is a coverage/scope decision for Nazim (console owner) — I recommend closing it or documenting+escalating the cut per #31843. Advisory only; no money/PII/schema fork. This review is of the GATE MECHANISM (routed per #31843) — NOT a console deploy sign-off; no `cc-quality-review.md` is saved under `reports/console-deploy/<hash>/` (and the gate is correctly refusing that deploy on the item-4a red test).

---

## RE-CONFIRM — fast-follow `0794e18` (2026-08-23, bus #31862)
Both findings closed; verified source + execution + mutation. **Verdict: PASS — clear to land.** 13/13 gate tests green.

- **MEDIUM (doc honesty) — FIXED by honest documentation** (widen escalated to Nazim, per my deferral — correct; I don't adjudicate scope). Seam header now states coverage exactly (backend = all `*.py`; static = ONLY the 5 named) and names every cut as a written boundary: the ~12 other served static (`index/irsyad/docs/media .html+.js`, `app.js`, `manifest.json`, `icons/*`, with routes), the outside-import (`lane_token_resolver`, Nazim #31857 confirmed), and the Dockerfile. `deploy_console.sh` points at the seam as SSOT instead of overstating. Cross-checked the "DOES NOT COVER" list against `app.py::_serve_static` — accurate. The silent boundary is now written (#31843 satisfied).
- **LOW (per-file delimiter) — FIXED + mutation-proved.** Each file's content NUL-framed `\0FILE\0<rel>\0`. Removing that line → `test_cross_file_content_move_changes_the_hash` RED (`'966f2804…' != '966f2804…'` — identical hash without the delimiter = the exact collision). The delimiter closes it; dead-man's-switch unaffected.
- **Note:** delimiter re-baselines the content hash `510d5c8d25586ca3` → `04d49c03392d9594` — expected + safe (a review keyed to the old hash simply re-reviews).

**STILL OPEN (correctly, not in this commit):** the static-coverage WIDEN is escalated to Nazim — gate the other served static or ratify the cut. I flag; he decides; I review when it lands.

---

## WIDEN — commit `248b2ea` (2026-08-23, bus #31874) — item-4b CLOSE-OUT
Nazim chose Option 1 (gate ALL served static + Dockerfile). My twin static blindspot is CLOSED. Verified source + execution + mutation. **Verdict: PASS.** 20/20 tests green.

- **A (twin-blindspot dead-man) — mutation-proved.** `console_deploy_files_rel` now = `find static -type f` + `find *.py` + Dockerfile (`if [ -f ]`-guarded), globally LC_ALL=C sorted, no carve-out. Reverting the static widen → `test_app_js_edit`/`test_icon_edit`/`test_previously_ungated`/`test_real_served_static` RED. app.js (26KB SPA), binary icons, all served static now move the hash.
- **B (Dockerfile-absent robustness) — mutation-proved.** Reverting `if [ -f ]`→`[ -f ] && printf` → `test_manifest_works_without_a_dockerfile` RED (`assert 1==0`): the `&&` form returns non-zero when the Dockerfile is absent and pipefail fails the whole seam. The `if`-guard is load-bearing.
- **C (completeness) — confirmed.** Gate's static set byte-exactly equals `find static -type f` (diff empty); Dockerfile gated. `app.py::_serve_static` resolves only within `_STATIC_DIR` (L2819-20 traversal guard) → all served content is under static/, fully gated. The L310 read is a token file, not served content.
- **D (binary assets) — confirmed.** A binary `.ico` byte-flip moves the hash (bytes flow through `cat` + NUL delimiter into shasum, no text-mode/NUL-truncation issue). Real `icon-192.png` gated.
- **E (re-baseline) — confirmed safe.** `04d49c03` → `f44d0afdc558cc61`; no review dir exists for the new hash, every existing `cc-quality-review.md` sits under an old hash dir → no stale review can match the widened tree.

Only remaining written cut: outside-package imports (`lane_token_resolver`) — concurred #31859, Nazim #31857. No silent boundary left. Micro-note: gating all of static/ means a stray/generated file there would rotate the hash (tree clean today; `__pycache__` excluded) — safe direction.

**item-4b DONE:** `5a787f1` (backend) + `0794e18` (doc+delimiter) + `248b2ea` (widen). No cai escalation.
