# cc-quality review — console content `b073ecf8001fd348`

**Verdict: PASS** (ship-clear for `scripts/deploy_console.sh`; deploy is orch-console/Musa's to run — I do not deploy).

- **Reviewer:** cc-quality (Head of Quality) — model `claude-sonnet-5` (op#24365/CAI-RESP-1440 arrangement). Non-FULL, non-money/PII review (pure display-order fix), sonnet-advisory gate does not apply.
- **Branch:** `fix/glm-card-window-order` (worktree `orchestrator.wt-glm-order`), head `d2899c5` — *"fix(console): GLM card shows weekly window before 5h, matching Max-pool cards (op#25348)"*.
- **Content hash:** recomputed myself via `console_content_hash` against this worktree = **`b073ecf8001fd348`** — MATCHES the requested hash.

## What ships

`nervous_system/console/glm_usage.py`'s `parse_quota()` now sorts the GLM quota card's `windows` list by length **descending** (`reverse=True`) instead of ascending, so the weekly window renders before the 5h window — matching the Claude Max-pool cards' row order. `tests/console/test_glm_usage.py`'s two order-dependent assertions updated to match.

## Verification (verify-not-assert)

1. **Confirmed the frontend has no independent ordering logic that would make this backend change moot.** Read `fleet.js` directly: `poolChip()` (Max-pool cards) hardcodes `poolWindowRow("wk", ...)` then `poolWindowRow("5h", ...)` — literally wk-first, matching the PR's claim about the "Max-pool card template" exactly. `glmCard()`'s row-building (`ws.map(...)`) iterates `g.windows` in **array order** with no sort/reorder of its own — so the backend's list order is what actually determines the rendered row order. This isn't a cosmetic no-op; it's the real fix.
2. **Sort direction is correct for the stated intent.** `_len_s` is window length in seconds (5h ≪ 1wk); `reverse=True` on ascending length sorts longest-first, i.e. wk before 5h. Matches "wk before 5h" exactly.
3. **Completeness sweep for other positional consumers of `windows`.** Grepped `nervous_system/console/*.py` for `windows[0]`/`windows[1]` — zero hits outside the two now-updated test files; `app.py` passes the list straight through to the JSON payload without caring about order. No sibling site was missed.
4. **Test correctness, not just presence.** `test_good_read_returns_numbers`: `wk, w5 = out["windows"]` then asserts `w5`'s label is `"5h"` — this is a real ordering assertion, not vacuous (if the sort reverted, `w5` would actually be bound to the wk dict and the label assertion would fail). `test_api_fleet_carries_glm_numbers_and_never_the_key`: updated to expect `["wk", "5h"]` and `windows[0]["cap"] == 60000` (the weekly cap, not the 5h cap) — correctly re-derived, not just swapped labels while leaving the wrong cap value.
5. **Ran both suites myself**, not trusted from the request: `tests/console/test_glm_usage.py` — 21/21 passed. `tests/console/fleet_glm.test.js` (node) — 8/8 passed.

## Render-gate limitation (noted, not a blocker)

Agreed this is a real, pre-existing structural gap orch-console flagged: the Playwright render harness replays a pre-fetched API snapshot, so a backend-only reorder like this one can't actually be exercised by the render step pre-merge — the committed `fleet.png`/`lanes.png` here are copied from a live-checkout render, not proof of this specific diff's visual effect. Not blocking (the test-level verification above is sufficient for a 2-line, fully-covered reorder), but worth a future fix if backend-only console changes become more visually consequential than this one.

## Verdict

**PASS.** Small, correctly-scoped, completely covered fix. No findings.
