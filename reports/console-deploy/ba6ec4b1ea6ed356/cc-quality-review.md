# cc-quality review — console content `ba6ec4b1ea6ed356` (op#34916: R4 enables on Musa's verified word, not a cai sign-off)

- **Reviewer:** cc-quality (Head of Quality). Ran on claude-opus-4-8.
- **Target:** content hash `ba6ec4b1ea6ed356` = commit `892e21c` on `fh/op34916-cai-advisory-a2-a3-a4`, base `origin/fable/substrate-safe-fixes`, worktree `orchestrator.wt-op34916`. Hash independently recomputed via the manifest seam ✓; tree clean.
- **Request:** bus #78875 (cc-fleet-health). Pre-push design gate reads this file; merge is separately HELD for Musa's console yes (#78864).
- **Scope:** engineering correctness, authorization-path security, fail-closed behavior, test completeness. **Not** the aesthetic eyeball (none — no UI change).
- **Date:** 2026-10-28.

## Verdict: CODE behavior **PASS** (correct + fail-safe, verified live OFF) — but **SHIP-BLOCKED on a test regression**: this change orphaned 23 pre-existing apply-armed / governance-set tests. Clear to deploy once those are migrated to the new enable mechanism (I'll re-review on the fix).

The production change itself is right and safe. The single blocker is that the suite is now RED on the exact destructive paths this change touches, and the deploy gate is blind to it (Gate-2 runs only `test_app.py`).

---

## What I verified clean (the production change)

**Authorization contract — sound, fail-closed.** `_r4_enabled()` resolves via the real `scripts/lib/require_verified_authorization.verified_authorization`, returning `AuthResult(ok, reason, row)`. Every uncertainty is DENY: no `after` timestamp, `MUSA_TELEGRAM_ID` unset, no substrate DSN, misconfigured phrases/tokens, or a DB-query exception all return `ok=False`. The candidate query is hard-scoped `direction='inbound' AND channel='telegram' AND chat_id=<MUSA_TELEGRAM_ID> AND created_at > after` — an in-console/tmux "YES" can never satisfy it. `_r4_enabled()`'s own `except Exception` is fail-closed (`(False, "enable-check-error:…")`).

**Enable vs arm cannot collide.** Phrase match is full-substring (`any(p in text …)`). Enable uses `"ENABLE LANE APPLY"`; the per-apply arm (`_r4_current_arm`) uses `"ARM LANE APPLY"`. Neither phrase is a substring of the other, so an enable message cannot satisfy the arm gate and vice-versa. Same verifier, same call shape as the arm already uses — confirmed by reading both call sites.

**Per-apply safety rail untouched.** The diff changes only the feature-enable check at the three gates. The typed `confirm==session` fat-finger guard, gazzabyte fail-closed, the single switch rail with post-hoc `auth_fp` verify, and inflight/cooldown are byte-unchanged. Money / irreversible ops still require the separate per-apply bridge arm (`_r4_current_arm` / `require_verified_authorization`). The queued-apply watcher re-checks `_r4_enabled()[0]` at fire time.

**Live, read-only — fail-safe.** Ran `_r4_enabled()` against the real substrate (SELECT on `operator_messages`, read-only) with `CONSOLE_R4_ENABLED` unset: returns **False** — "no verified Musa enable". `MUSA_TELEGRAM_ID` + `DATABASE_URL` resolve, the verifier reaches the DB and finds no matching row. So the change does **not** turn R4 on; it stays OFF exactly as today until Musa sends the phrase, and the live wiring resolves end-to-end.

**New tests pass.** `tests/test_console_r4_enable_op34916.py` — 6/6, I ran them (env-alone, Musa-alone w/ op_id+phrase assert, neither, verifier-error-fail-closed, cache-dedup, source-assertion that no `cai sign-off`/bare-`_R4_ENABLED`-gate remains and `_r4_enabled()` is used ≥3×).

---

## BLOCKER — test completeness: 23 pre-existing tests orphaned on the changed destructive paths

The three gates moved off the module constant `_R4_ENABLED` onto the `_r4_enabled()` function. `_R4_ENABLED` still exists (boot-time) but **nothing consults it** — the new test even asserts `"if not _R4_ENABLED:" not in SRC`. But 23 pre-existing tests still enable/disable R4 by `monkeypatch.setattr(console_app, "_R4_ENABLED", True/False)` (now a silent no-op) or assert the old 503 text `"CONSOLE_R4_ENABLED off"`. Full `tests/console/` run (no-DB sanctioned mode): **23 failed, 436 passed**, every failure in:

- `tests/console/test_governance.py` — 18 (all `test_set_*`: body/confirm/reason/money-ack validation, success-argv, rc-mapping, `r4_off_503`)
- `tests/console/test_lane_actions.py` — 3 (`apply_armed_fires…NO_bridge_arm`, `confirm_mismatch_400`, `resolver_rules_still_fail_closed`)
- `tests/console/test_armed_bearer.py` — 2 (`governance_set_reaches_the_r4_gate`, `apply_armed_400_on_empty_body_when_r4_enabled`)

**Proven to be stale mechanism, not a behavioral regression.** Re-running those three files with the real env switch `CONSOLE_R4_ENABLED=1` (which makes `_r4_enabled()` return True early — the intended "R4 on" state) takes **23 → 4**; the 4 survivors are precisely the tests asserting the R4-**OFF** direction (`test_set_r4_off_503_no_spawn`, `test_apply_armed_503_without_r4_flag`) or the old 503 string — i.e. they contradict a forced-ON env and pass once migrated to drive `_r4_enabled` honestly. So the gate behaves correctly in both directions; the tests just still pull the dead lever.

**Why this blocks (not a MINOR):**
1. These were the regression signal for the money/irreversible rail — apply-armed *fires* with typed confirm, confirm-mismatch 400s with no subprocess, resolver stays fail-closed, governance-set emits the exact argv. They are now **always-red / dead signal** on exactly the paths this change reaches into.
2. `scripts/deploy_console.sh` Gate-2 runs **only `tests/console/test_app.py`** (green), so the deploy gate will **not** catch this rot. The "106 passed" in the request came from the `test_console_*` / oracle / additive files — it never ran `test_lane_actions.py` / `test_governance.py` / `test_armed_bearer.py`.

**Fix (small, mechanical):** migrate the 23 to the new enable path — set `CONSOLE_R4_ENABLED=1` via `monkeypatch.setenv` (or inject `verify=` as the new op#34916 tests do) instead of `setattr(_R4_ENABLED, …)`, and update the OFF-state assertions to the new 503 text. While there, add a case that opens the gate via a *verified-Musa-enable* stub (not just the env switch) on at least one apply-armed and one governance-set test, so the new primary path has downstream coverage. Re-run full `tests/console/` to green. Ping me and I'll clear it.

---

## Advisory alerts (escalate; cai's call per charter cond. #2 — I surface, don't adjudicate)

**Your Q1 — should Musa's word also enable `/api/governance-set`? (MEDIUM, scope.)** As written, yes: governance-set shares the same `_r4_enabled()`. But the phrase `"ENABLE LANE APPLY"` semantically names *lane apply*, and a single such message now also unlocks **governance writes** for 24h. The coupling pre-dates this change (both keyed off the env flag), but routing it through an apply-named phrase makes the scope-widening less visible. Recommend either a distinct phrase/op for governance writes (e.g. `"ENABLE GOVERNANCE WRITES"`, `op_tokens=["governance"]`) or an explicit Musa/cai ruling that the coupling is intended. This is a governance-scope question → cai.

**Your Q2 — is 24h right? (judgment.)** 24h is long for a standing enable of destructive apply **and** governance writes from one message. The per-apply guards remain, so the blast radius per op is bounded, but the *window* is wide. The per-apply arm caps at 240min/4h (`_R4_ARM_MAX_WINDOW_MIN`); a shorter enable window (or matching that 4h) would be more defensible. Policy call — defer to Musa/cai; flagging that one phrase holds both rails open for a full day.

**MINOR — 60s cache stale window.** `_R4_ENABLE_CACHE` caches the (True/False) result 60s. Effective enable window is therefore 24h **+ up to 60s**, and the queued-apply watcher's "re-check at fire time" is up to 60s stale — a cached True survives ~60s past expiry/revocation. Low-risk (reversible token/model switch only; money/irreversible still gated by the separate per-apply arm), but the header comment implies the watcher re-checks fresh every pass; it re-checks a ≤60s-cached value.

**MINOR — op_tokens inert for this call.** `op_tokens=["enable","lane","apply"]` are all substrings of the phrase `"ENABLE LANE APPLY"`, and token-match is any-of, so the token check adds no constraint beyond the phrase. Not a hole (the phrase is a strong, distinct discriminator), just note the op-scoping mechanism is doing nothing here.

---

## Bottom line

Production code for `ba6ec4b1ea6ed356` is **correct and fail-safe** — R4 stays OFF (verified live) until a bridge-verified Musa `"ENABLE LANE APPLY"` lands, the verifier is the same fail-closed gate the arm uses, and the per-apply rail is untouched. **Clearance is withheld only on the test regression:** 23 orphaned tests must be migrated to the new enable mechanism so the destructive apply + governance rail regains live regression signal (Gate-2 won't catch it for you). Fix those to green, ping me, and I'll clear to deploy. The governance-set coupling and the 24h window are advisory → cai/Musa.

Ran on claude-opus-4-8.
