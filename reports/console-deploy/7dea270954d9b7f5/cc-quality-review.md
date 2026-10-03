# cc-quality review — console content `7dea270954d9b7f5`

**Verdict: PASS** (ship-clear for `scripts/deploy_console.sh`; deploy is orch-console/Musa's to run — I do not deploy).

- **Reviewer:** cc-quality (Head of Quality) — model `claude-sonnet-5` (op#24365/CAI-RESP-1440 arrangement). Security-adjacent surface (leak-flagged key visibility), but this is a UI-visibility change with the underlying control verified untouched — not a FULL money/PII/live-tenant verdict, so the opus-confirmation gate does not apply.
- **Branch:** `fix/glm-card-window-order` (worktree `orchestrator.wt-glm-order`), head `d91f897` — *"fix(console): hide the GLM key-leak warning banner, track rotation via commitment (op#25357)"*, stacked on the already-reviewed `d2899c5` (content `b073ecf8001fd348`).
- **Content hash:** recomputed myself via `console_content_hash` against this worktree = **`7dea270954d9b7f5`** — MATCHES the requested hash.

## What ships

`fleet.js`'s `glmCard()` no longer renders the `glmwarn` banner div for a leak-flagged-but-accepted GLM key. `glm_usage.py` gets a docstring-only update (no functional change). `fleet_glm.test.js`'s 3 warning-related tests become 2 asserting the banner never renders.

## The actual question I was asked to answer: did this quietly weaken the control, or just its visibility?

**Verified: visibility only, control fully intact.** Checked every layer myself, not taken from the description:

1. **The real leak-flag/accepted-use logic in `glm_usage.py` is completely untouched** — grepped the whole file: `if secret.leak_flagged: accepted = ACCEPTED_LEAK_FLAG.get(VAULT_NAME); ...` and the `_key_warning` assignment logic (lines 92-119) are identical to before this diff; the only change in this file is the module docstring's description of what the UI does with the value.
2. **The backend API still returns `key_warning` truthfully.** `out["key_warning"] = _key_warning` (line 214) is unchanged, and `app.py`'s `/api/fleet` handler passes the full `glm` dict through via `_jsonable(glm)` (line 1796) with no filtering — anything reading the raw API (not just the rendered card) still sees the real value.
3. **No stray rendering path left for the string.** Read the full `glmCard()` function: the `title` tooltip string (built from name/windows/age) never included `key_warning` either, before or after this change — there is no leftover DOM-insertion site for it anywhere in this function now. This is why removing the "key_warning is escaped" test is correct, not a coverage loss: there's genuinely nothing left to escape-test, since the string is never written to innerHTML anywhere in this function any more.
4. **The compensating control is real, not just a comment.** Verified live in the substrate DB: `held_commitments` id 187, `owner_agent='orch-console'`, `due_at` = `created_at` + exactly 30 days, `status='pending'`, payload explicitly names `vault_name: GLM_CODING_KEY`, `accepted_ref: op#24626`, and frames itself as *"the compensating control"* for this exact UI change (`op#25357`). This is a live, attributable, dated tracking row, not a promise that could get silently forgotten.
5. **Tests are honest about intent, not just passing.** The two replacement tests assert the banner is `!html.includes("glmwarn")`/`!html.includes("op#24626")` even with `key_warning` explicitly set in the fixture — this proves the hiding is deliberate behavior under test, not an accidental side effect that happened to also pass.
6. **Ran the suite myself:** `fleet_glm.test.js` — 7/7 passed (confirmed the new test names/assertions, not stale from a prior run).

## Verdict

**PASS.** This is exactly what it claims to be: a UI-visibility change with the underlying leak-flag/accepted-use control, the backend's honest API output, and the audit trail all verified fully intact. The hidden risk now has a dated, attributable, live commitment row standing in for the UI nag — a real compensating control, not a silent weakening.
