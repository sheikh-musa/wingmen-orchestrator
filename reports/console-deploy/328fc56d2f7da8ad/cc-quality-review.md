# cc-quality review — console content `328fc56d2f7da8ad` (op#34916 R4-enable, re-review)

- **Reviewer:** cc-quality (Head of Quality). Ran on claude-opus-4-8.
- **Target:** content hash `328fc56d2f7da8ad` = commit `cb0e165` on `fh/op34916-cai-advisory-a2-a3-a4`, worktree `orchestrator.wt-op34916`. Hash independently recomputed ✓; tree clean.
- **Supersedes:** my verdict for `ba6ec4b1ea6ed356` (#78892) — the ship-blocking test regression there is now resolved.
- **Request:** bus #78908 (cc-fleet-health).
- **Date:** 2026-10-28.

## Verdict: CODE **PASS** — cleared to deploy. The prior blocker is fixed at the root, both advisory questions are resolved, and the full console suite is green (I ran it). Merge remains HELD for Musa's console yes (#78864); R4 stays OFF until a verified Musa enable lands.

My `ba6ec4b1ea6ed356` verdict cleared the production authorization code but blocked on 23 orphaned tests. This revision fixes that correctly and addresses both advisory questions. No open findings.

---

## The blocker (23 stale tests) — RESOLVED, and fixed the right way

Rather than rewrite the tests around the new helper, the fix **kept the module constant `_R4_ENABLED` as a real input**: `_r4_enabled(scope="apply", *, env_on=None, verify=None, now=None)`. 
- The endpoints pass `env_on=_R4_ENABLED` — the boot-time constant, exactly the switch source `_handle_apply_armed`/`_handle_governance_set` used **before** op#34916. So every existing `monkeypatch.setattr(console_app, "_R4_ENABLED", …)` test means what it always meant.
- The queued-apply watcher passes nothing → `env_on=None` → reads the **live** env, exactly as it always did.
- Musa's verified enable is purely additive on top of each site's original switch.

**Verified by running the full suite myself** (the exact gap that caused my miss last round — `deploy_console.sh` Gate-2 only runs `test_app.py`): `env -u DATABASE_URL -u CONSOLE_DB_URL -u SUPABASE_DB_URL -u CONSOLE_R4_ENABLED … pytest tests/console/ -q` → **463 passed, 0 failed** (was 23 failed / 436 passed at the prior hash). The three previously-red files (`test_governance.py`, `test_lane_actions.py`, `test_armed_bearer.py`) are green. `tests/test_console_r4_enable_op34916.py` → **9 passed** (6 → 9).

## Advisory questions — both resolved

- **Q1 (governance coupling) — decoupled.** `_R4_SCOPES` gives governance its own op/phrase: `("console-governance-enable", "ENABLE GOVERNANCE WRITES", ["enable","governance","writes"])` vs apply's `("console-r4-enable", "ENABLE LANE APPLY", ["enable","lane","apply"])`. Neither phrase is a substring of the other, and the verifier checks the phrase **and** a token (`any(p in text) and any(t in text)`), with the phrase as the discriminator — so an apply enable can never open governance and vice-versa. Locked by `test_set_NOT_enabled_by_the_apply_phrase` and `test_governance_scope_has_its_own_phrase…` (asserts `_R4_ENABLE_PHRASE != _GOV_ENABLE_PHRASE`). The shared `CONSOLE_R4_ENABLED` env switch still opens both — that is the intended operator/cai master override, which was never the coupling I flagged.
- **Q2 (window) — 4h.** `_R4_ENABLE_WINDOW_H = 4`, matching the per-apply arm cap `_R4_ARM_MAX_WINDOW_MIN` (240 min). Pinned by `test_enable_window_matches_the_4h_per_apply_arm_cap` (`== 4 == _R4_ARM_MAX_WINDOW_MIN // 60`).

## Downstream coverage added (as requested)
- `test_apply_armed_fires_on_musa_verified_enable_without_env_flag` — apply-armed FIRES via the new Musa-verified path with `_R4_ENABLED=False` and no env flag; still 503 when neither (`test_apply_armed_still_503_with_no_flag_and_no_musa_enable`).
- `test_set_enabled_by_musa_verified_governance_phrase` — governance-set enables on its own phrase.
- `test_explicit_env_on_false_ignores_a_live_env_flag` — the endpoint's boot-constant source is authoritative over a live env flag.

## Security re-verification (what I checked, not assumed)
- **Authorization contract unchanged & fail-closed** — still the real `verified_authorization` (chat-scoped to `MUSA_TELEGRAM_ID`, `created_at > after`, DB-error/missing-config → DENY); `_r4_enabled`'s `except` is fail-closed. Per-apply rail (typed confirm, gazzabyte, switch rail auth_fp, inflight/cooldown) and the money/irreversible `_r4_current_arm` are untouched.
- **Per-scope cache** — `_R4_ENABLE_CACHE[scope]` is now keyed by scope, so an `apply=True` result cannot poison the `governance` gate (a real improvement over the single-key cache).
- **Live read-only, both scopes** — against the real substrate with `env_on=False` (forcing the verifier path): **apply = OFF**, **governance = OFF**, each citing its own phrase/op and the 4h `after` bound. Fail-safe; wiring resolves end-to-end. R4 stays OFF until Musa sends the respective phrase.

## MINORs (non-blocking)
- **60s per-scope cache** — effective enable window is 4h + up to 60s; the queue watcher re-check is up to 60s stale. Low-risk (reversible token/model switch only; money/irreversible still gated by the separate per-apply arm). Acknowledged by the author.
- **`_R4_SCOPES[scope]` is outside the `try`** — an unknown `scope` would `KeyError` (not fail-closed). Not reachable today (all three call sites pass a hardcoded literal); a trivial hardening would be to validate/`.get()` the scope or move the lookup inside the guarded block. Optional.

## Bottom line
`328fc56d2f7da8ad` is **cleared to deploy**. The authorization code is correct and fail-safe (both scopes verified OFF live), the 23-test regression is fixed at the root with each call site keeping its original switch source, governance is decoupled onto its own phrase, the window is the 4h arm cap, and the full `tests/console/` is green on my own run. Merge stays held for Musa (#78864); aesthetic sign-off is orch-console's per 77019.

Ran on claude-opus-4-8.
